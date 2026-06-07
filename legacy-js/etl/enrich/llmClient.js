/**
 * LLM Client — Groq API Integration
 *
 * Calls the Groq API to generate missing medicine fields using LLM inference.
 * Uses llama-3.3-70b-versatile for high-quality pharmaceutical data generation.
 *
 * Supports multi-key rotation: supply 1-4 Groq API keys to distribute
 * requests across free-tier accounts and auto-failover on 429 rate limits.
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

// ─── Multi-Key Rotator ──────────────────────────────────────────────
/**
 * Manages multiple Groq API keys with round-robin rotation.
 * Auto-switches on 429 rate limits with per-key cooldown tracking.
 */
class GroqKeyRotator {
  /**
   * @param {string[]} apiKeys - Array of Groq API keys
   * @param {number} cooldownMs - How long to sideline a rate-limited key (default 60s)
   */
  constructor(apiKeys, cooldownMs = 60000) {
    if (!apiKeys || apiKeys.length === 0) {
      throw new Error("GroqKeyRotator requires at least one API key");
    }
    this.keys = apiKeys.map((key, index) => ({
      key,
      index,
      cooldownUntil: 0,  // timestamp when cooldown expires
      requestCount: 0,
      errorCount: 0,
    }));
    this.currentIndex = 0;
    this.cooldownMs = cooldownMs;
  }

  /** Get the next available key, skipping any that are in cooldown */
  getKey() {
    const now = Date.now();
    const totalKeys = this.keys.length;

    // Try each key starting from currentIndex
    for (let attempt = 0; attempt < totalKeys; attempt++) {
      const idx = (this.currentIndex + attempt) % totalKeys;
      const entry = this.keys[idx];

      if (entry.cooldownUntil <= now) {
        this.currentIndex = (idx + 1) % totalKeys; // advance for next call
        entry.requestCount++;
        return entry;
      }
    }

    // All keys are in cooldown — return the one with the soonest expiry
    const soonest = this.keys.reduce((a, b) => a.cooldownUntil < b.cooldownUntil ? a : b);
    const waitMs = soonest.cooldownUntil - now;
    console.log(`[KeyRotator] ⏳ All ${totalKeys} keys rate-limited. Waiting ${Math.ceil(waitMs / 1000)}s for key #${soonest.index + 1}...`);
    return { ...soonest, waitMs };
  }

  /** Mark a key as rate-limited (429). Applies cooldown. */
  markRateLimited(keyEntry, retryAfterMs) {
    const cooldown = retryAfterMs || this.cooldownMs;
    keyEntry.cooldownUntil = Date.now() + cooldown;
    keyEntry.errorCount++;
    console.log(`[KeyRotator] 🔄 Key #${keyEntry.index + 1} rate-limited. Cooldown ${Math.ceil(cooldown / 1000)}s. Switching to next key.`);
  }

  /** Get stats for logging */
  getStats() {
    return this.keys.map(k => ({
      key: `#${k.index + 1} (…${k.key.slice(-6)})`,
      requests: k.requestCount,
      errors: k.errorCount,
      inCooldown: k.cooldownUntil > Date.now(),
    }));
  }
}

// Singleton rotator — initialized on first use
let _rotator = null;

/**
 * Initialize or get the key rotator.
 * @param {string|string[]} apiKeys - Single key or array of keys
 * @returns {GroqKeyRotator}
 */
function getRotator(apiKeys) {
  if (_rotator) return _rotator;
  const keys = Array.isArray(apiKeys) ? apiKeys : [apiKeys];
  _rotator = new GroqKeyRotator(keys.filter(Boolean));
  console.log(`[KeyRotator] Initialized with ${_rotator.keys.length} API key(s)`);
  return _rotator;
}

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
 * Supports multi-key rotation — automatically retries with the next key on 429.
 *
 * @param {object} medicine - MongoDB medicine document
 * @param {object} options
 * @param {string|string[]} options.apiKey - Single Groq API key or array of keys
 * @param {string[]} [options.missingFields] - Override auto-detected missing fields
 * @returns {Promise<object>} - { success, enrichedFields, fieldsUpdated, error }
 */
async function enrichMedicine(medicine, options = {}) {
  const { apiKey } = options;

  if (!apiKey || (Array.isArray(apiKey) && apiKey.filter(Boolean).length === 0)) {
    return { success: false, enrichedFields: {}, fieldsUpdated: [], error: "No GROQ_API_KEY provided" };
  }

  const missingFields = options.missingFields || detectMissingFields(medicine);

  if (missingFields.length === 0) {
    return { success: true, enrichedFields: {}, fieldsUpdated: [], error: null };
  }

  const prompt = buildEnrichmentPrompt(medicine, missingFields);
  const rotator = getRotator(apiKey);
  const maxRetries = rotator.keys.length; // Try each key at most once per request

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const keyEntry = rotator.getKey();

    // If all keys are in cooldown, wait for the soonest one
    if (keyEntry.waitMs) {
      await new Promise(r => setTimeout(r, keyEntry.waitMs));
    }

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
            Authorization: `Bearer ${keyEntry.key}`,
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

      // Rate limit — mark this key as limited and try next one
      if (statusCode === 429) {
        // Parse retry-after header if available (in seconds)
        const retryAfterSec = parseInt(error.response?.headers?.["retry-after"], 10);
        const cooldownMs = retryAfterSec ? retryAfterSec * 1000 : 60000;
        rotator.markRateLimited(keyEntry, cooldownMs);

        // If we have more keys to try, continue the loop
        if (attempt < maxRetries) {
          continue;
        }

        // All keys exhausted for this request
        return {
          success: false,
          enrichedFields: {},
          fieldsUpdated: [],
          error: `All ${rotator.keys.length} keys rate-limited. ${errorMessage}`,
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
}

module.exports = {
  enrichMedicine,
  detectMissingFields,
  buildEnrichmentPrompt,
  parseLLMResponse,
  validateAndSanitize,
  GroqKeyRotator,
  getRotator,
  ENRICHABLE_FIELDS,
  GROQ_MODEL,
};
