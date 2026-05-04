/**
 * LLM Client — Groq API Integration
 *
 * Calls the Groq API to generate missing medicine fields using LLM inference.
 * Uses llama-3.3-70b-versatile for high-quality pharmaceutical data generation.
 *
 * No SDK dependency — uses Axios REST calls directly.
 */

const axios = require("axios");
const { cleanDescription, cleanSideEffects, cleanFaq } = require("../transform/sanitizer");

// ─── Configuration ──────────────────────────────────────────────────
const GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_MODEL = "llama-3.3-70b-versatile";
const GROQ_MAX_TOKENS = 1024;
const GROQ_TEMPERATURE = 0.3; // Low temp for factual accuracy

// Fields the LLM can populate
const ENRICHABLE_FIELDS = ["description", "side_effects", "faq"];

/**
 * Determine which fields are missing/empty for a medicine document.
 *
 * @param {object} medicine - MongoDB medicine document
 * @returns {string[]} - List of missing enrichable field names
 */
function detectMissingFields(medicine) {
  const missing = [];

  if (!medicine.description || medicine.description.trim().length < 10) {
    missing.push("description");
  }
  if (!Array.isArray(medicine.side_effects) || medicine.side_effects.length === 0) {
    missing.push("side_effects");
  }
  if (!Array.isArray(medicine.faq) || medicine.faq.length === 0) {
    missing.push("faq");
  }

  return missing;
}

/**
 * Build a structured prompt requesting only the missing fields.
 *
 * @param {object} medicine - MongoDB medicine document
 * @param {string[]} missingFields - Which fields to request
 * @returns {string} - The prompt string
 */
function buildEnrichmentPrompt(medicine, missingFields) {
  const identity = [
    `Name: ${medicine.name || "Unknown"}`,
    medicine.salt ? `Salt: ${medicine.salt.substring(0, 150)}` : null,
    medicine.normalized_salt ? `Active: ${medicine.normalized_salt.substring(0, 150)}` : null,
    medicine.manufacturer ? `Mfg: ${medicine.manufacturer.substring(0, 100)}` : null,
    medicine.dosage ? `Dose: ${medicine.dosage}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  const fieldInstructions = [];

  if (missingFields.includes("description")) {
    fieldInstructions.push(
      `"description": "2-4 sentence medical description. Uses, mechanism, notes. Factual, no marketing."`
    );
  }

  if (missingFields.includes("side_effects")) {
    fieldInstructions.push(
      `"side_effects": ["effect1", "effect2"] (5-10 common side effects, lowercase, single words)`
    );
  }

  if (missingFields.includes("faq")) {
    fieldInstructions.push(
      `"faq": [{"question": "...", "answer": "..."}] (3 common patient Q&As, 1-2 sentence answers)`
    );
  }

  return `Medicine:
${identity}

Return JSON with:
{
  ${fieldInstructions.join(",\n  ")}
}

RULES:
- JSON only. No markdown/code blocks.
- Factual & clinical. No marketing or disclaimers.
- Do not guess dosage/salt.
- Concise.`;
}

/**
 * Parse the LLM JSON response with fallback handling.
 *
 * @param {string} responseText - Raw LLM output
 * @returns {object|null} - Parsed JSON or null
 */
function parseLLMResponse(responseText) {
  if (!responseText || typeof responseText !== "string") return null;

  let text = responseText.trim();

  // Strip markdown code fences if present (models sometimes add them despite instructions)
  text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```\s*$/, "");

  // Try direct parse
  try {
    return JSON.parse(text);
  } catch {
    // Try to extract JSON object from the text
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      try {
        return JSON.parse(jsonMatch[0]);
      } catch {
        return null;
      }
    }
    return null;
  }
}

/**
 * Validate and sanitize the LLM-generated fields through existing sanitizers.
 *
 * @param {object} generated - Raw parsed LLM response
 * @param {string[]} requestedFields - Which fields were requested
 * @returns {object} - Cleaned and validated fields
 */
function validateAndSanitize(generated, requestedFields) {
  if (!generated || typeof generated !== "object") return {};

  const sanitized = {};

  if (requestedFields.includes("description") && generated.description) {
    const cleaned = cleanDescription(String(generated.description));
    if (cleaned && cleaned.length >= 20) {
      sanitized.description = cleaned;
    }
  }

  // Explicitly strip dosage/composition if hallucinated
  delete sanitized.dosage;
  delete sanitized.composition;
  delete sanitized.salt;

  if (requestedFields.includes("side_effects") && generated.side_effects) {
    const cleaned = cleanSideEffects(generated.side_effects);
    if (cleaned.length >= 2) {
      sanitized.side_effects = cleaned;
    }
  }

  if (requestedFields.includes("faq") && generated.faq) {
    const cleaned = cleanFaq(generated.faq);
    if (cleaned.length >= 1) {
      sanitized.faq = cleaned;
    }
  }

  return sanitized;
}

/**
 * Call the Groq API to enrich a single medicine.
 *
 * @param {object} medicine - MongoDB medicine document
 * @param {object} options
 * @param {string} options.apiKey - Groq API key
 * @param {string[]} [options.missingFields] - Override auto-detected missing fields
 * @returns {Promise<object>} - { success, enrichedFields, fieldsUpdated, error }
 */
async function enrichMedicine(medicine, options = {}) {
  const { apiKey } = options;

  if (!apiKey) {
    return { success: false, enrichedFields: {}, fieldsUpdated: [], error: "No GROQ_API_KEY provided" };
  }

  const missingFields = options.missingFields || detectMissingFields(medicine);

  if (missingFields.length === 0) {
    return { success: true, enrichedFields: {}, fieldsUpdated: [], error: null };
  }

  const prompt = buildEnrichmentPrompt(medicine, missingFields);

  try {
    const response = await axios.post(
      GROQ_API_URL,
      {
        model: GROQ_MODEL,
        messages: [
          {
            role: "system",
            content: "Return JSON only.",
          },
          {
            role: "user",
            content: prompt,
          },
        ],
        temperature: GROQ_TEMPERATURE,
        max_tokens: GROQ_MAX_TOKENS,
        response_format: { type: "json_object" },
      },
      {
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        timeout: 30000,
      }
    );

    const rawContent = response.data?.choices?.[0]?.message?.content;
    const parsed = parseLLMResponse(rawContent);

    if (!parsed) {
      return {
        success: false,
        enrichedFields: {},
        fieldsUpdated: [],
        error: `Failed to parse LLM response: ${rawContent?.substring(0, 200)}`,
      };
    }

    const sanitized = validateAndSanitize(parsed, missingFields);
    const fieldsUpdated = Object.keys(sanitized);

    return {
      success: fieldsUpdated.length > 0,
      enrichedFields: sanitized,
      fieldsUpdated,
      error: fieldsUpdated.length === 0 ? "LLM response did not produce valid fields" : null,
    };
  } catch (error) {
    const statusCode = error.response?.status;
    const errorMessage = error.response?.data?.error?.message || error.message;

    // Rate limit handling
    if (statusCode === 429) {
      return {
        success: false,
        enrichedFields: {},
        fieldsUpdated: [],
        error: `Rate limited by Groq. Wait and retry. ${errorMessage}`,
        retryable: true,
      };
    }

    return {
      success: false,
      enrichedFields: {},
      fieldsUpdated: [],
      error: `Groq API error (${statusCode || "network"}): ${errorMessage}`,
    };
  }
}

module.exports = {
  enrichMedicine,
  detectMissingFields,
  buildEnrichmentPrompt,
  parseLLMResponse,
  validateAndSanitize,
  ENRICHABLE_FIELDS,
  GROQ_MODEL,
};
