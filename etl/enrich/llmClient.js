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
    `Medicine Name: ${medicine.name || "Unknown"}`,
    medicine.salt ? `Salt/Composition: ${medicine.salt}` : null,
    medicine.normalized_salt ? `Active Ingredient: ${medicine.normalized_salt}` : null,
    medicine.manufacturer ? `Manufacturer: ${medicine.manufacturer}` : null,
    medicine.dosage ? `Dosage: ${medicine.dosage}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  const fieldInstructions = [];

  if (missingFields.includes("description")) {
    fieldInstructions.push(
      `"description": A concise 2-4 sentence medical description of this medicine. Include what it is used for, how it works (mechanism), and important usage notes. No marketing language. Be factual and clinical.`
    );
  }

  if (missingFields.includes("side_effects")) {
    fieldInstructions.push(
      `"side_effects": An array of 5-10 common side effects as individual clean lowercase strings. Example: ["nausea", "headache", "dizziness", "fatigue"]. No sentences, just individual effect names.`
    );
  }

  if (missingFields.includes("faq")) {
    fieldInstructions.push(
      `"faq": An array of 3 common generic questions and answers about this medicine. Each entry must have "question" and "answer" keys. Questions should be what a patient would commonly ask. Answers should be 1-2 sentences, medically accurate.`
    );
  }

  return `You are a pharmaceutical database assistant. Your task is to generate accurate medical information for a medicine database.

Given the following medicine identity:
${identity}

Generate ONLY the following missing fields as a valid JSON object:
{
  ${fieldInstructions.join(",\n  ")}
}

CRITICAL RULES:
- Respond with ONLY the JSON object. No explanation, no markdown, no code fences.
- Be medically accurate and factual.
- Do NOT include marketing language, promotional text, or disclaimers.
- Do NOT generate or guess the dosage.
- Do NOT generate or guess the composition/salt.
- Do NOT provide specific treatment advice or prescriptive instructions.
- If you are unsure about a field, provide the most commonly accepted medical information.
- For side effects, list only the well-documented common ones.
- Keep descriptions concise and professional.`;
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
            content: "You are a pharmaceutical data generator. Always respond with valid JSON only. No explanations.",
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
