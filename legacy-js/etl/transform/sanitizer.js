/**
 * Data Sanitizer
 *
 * Centralized cleaning utilities for descriptions, FAQs, and side effects.
 * Handles:
 *   - HTML tag stripping and entity decoding
 *   - Description deduplication and section splitting
 *   - Side effect normalization into clean individual strings
 *   - FAQ HTML cleaning and deduplication
 */

// ─── HTML Entity Map ────────────────────────────────────────────────
const HTML_ENTITIES = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
  "&nbsp;": " ",
  "&ndash;": "–",
  "&mdash;": "—",
  "&laquo;": "«",
  "&raquo;": "»",
  "&bull;": "•",
  "&hellip;": "…",
  "&copy;": "©",
  "&reg;": "®",
  "&trade;": "™",
};

const HTML_ENTITY_REGEX = new RegExp(
  Object.keys(HTML_ENTITIES)
    .map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|"),
  "gi"
);

// Numeric HTML entities: &#123; or &#x1F;
const NUMERIC_ENTITY_REGEX = /&#(\d+);/g;
const HEX_ENTITY_REGEX = /&#x([0-9a-f]+);/gi;

// ─── Promotional / noise patterns to remove from descriptions ───────
const DESCRIPTION_NOISE_PATTERNS = [
  // Navigation / header noise from pharmacy sites
  /^quick\s+links?.*$/gim,
  /^(?:[\w\s]+\|){2,}[\w\s]+$/gim, // pipe-separated navigation: "Uses|How it works|Side effects"
  /^(?:introduction|synopsis|summary|quick\s+overview)\s*$/gim,
  /^useful\s+diagnostic\s+tests?\s*$/gim,
  /^how\s+to\s+manage\s+side\s+effects?\s*$/gim,
  /^warning\s*&\s*precautions?\s*$/gim,
  /^interactions?\s*$/gim,
  /^directions?\s+for\s+use\s*$/gim,
  // Promotional / disclaimer noise
  /disclaimer\s*:.*$/gim,
  /note\s*:.*the\s+information.*$/gim,
  /this\s+information\s+is\s+(not\s+)?intended.*$/gim,
  /consult\s+your\s+(doctor|physician|healthcare).*$/gim,
  /always\s+consult\s+a\s+(qualified|certified).*$/gim,
  /read\s+more\s*\.?\s*$/gim,
  /click\s+here\s*\.?\s*$/gim,
  /view\s+all\s*\.?\s*$/gim,
  /know\s+more\s*\.?\s*$/gim,
  /order\s+(now|online|today).*$/gim,
  /buy\s+(now|online|today).*$/gim,
  /free\s+(shipping|delivery).*$/gim,
  /cash\s+on\s+delivery.*$/gim,
  /\bavailable\s+at\b.*$/gim,
  /\blowest\s+price\b.*$/gim,
  /\bbest\s+price\b.*$/gim,
];

// ─── Side effect noise filters ──────────────────────────────────────
const SIDE_EFFECT_MIN_LENGTH = 3;
const SIDE_EFFECT_MAX_LENGTH = 50;
const SIDE_EFFECT_MAX_WORDS = 6;
const SIDE_EFFECT_NOISE_PATTERNS = [
  /^common\s+side\s+effects?\s*(include|are|of)?:?\s*/i,
  /^rare\s+side\s+effects?\s*(include|are|of)?:?\s*/i,
  /^serious\s+side\s+effects?\s*(include|are|of)?:?\s*/i,
  /^some\s+(common\s+)?side\s+effects?\s*(include|are|of)?:?\s*/i,
  /^the\s+(following|most\s+common)\s+side\s+effects?.*?:?\s*/i,
  /^\d+\s*\.\s*/, // numbered list prefix
  /^[-*•]\s*/, // bullet prefix
];

/**
 * Strip all HTML tags from a string.
 *
 * @param {string} text - Input text potentially containing HTML
 * @returns {string} - Clean text with no HTML tags
 */
function stripHtml(text) {
  if (!text || typeof text !== "string") return "";

  let cleaned = text;

  // Replace <br>, <br/>, <br />, </p>, </div>, </li> with newlines
  cleaned = cleaned.replace(/<\s*\/?\s*(?:br|p|div|li|tr)\s*\/?>/gi, "\n");

  // Remove all remaining HTML tags
  cleaned = cleaned.replace(/<[^>]*>/g, " ");

  // Decode named HTML entities
  cleaned = cleaned.replace(HTML_ENTITY_REGEX, (match) => {
    return HTML_ENTITIES[match.toLowerCase()] || match;
  });

  // Decode numeric HTML entities
  cleaned = cleaned.replace(NUMERIC_ENTITY_REGEX, (_, code) => {
    return String.fromCharCode(parseInt(code, 10));
  });

  // Decode hex HTML entities
  cleaned = cleaned.replace(HEX_ENTITY_REGEX, (_, hex) => {
    return String.fromCharCode(parseInt(hex, 16));
  });

  // Normalize whitespace
  cleaned = cleaned
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n/g, "\n")
    .trim();

  return cleaned;
}

/**
 * Clean a description field.
 *
 * Pipeline:
 *   1. Strip HTML
 *   2. Remove promotional / noise text
 *   3. Remove duplicate paragraph blocks
 *   4. Trim and normalize
 *
 * @param {string} rawDescription - Raw description text (may contain HTML)
 * @returns {string|null} - Cleaned description or null
 */
function cleanDescription(rawDescription) {
  if (!rawDescription || typeof rawDescription !== "string") return null;

  let description = stripHtml(rawDescription);

  // Remove noise patterns
  for (const pattern of DESCRIPTION_NOISE_PATTERNS) {
    description = description.replace(pattern, "");
  }

  // Split into sentences for better deduplication (pharmacy sites repeat entire paragraphs)
  // Use both newline and period-based splitting for wall-of-text descriptions
  let sentences = description
    .split(/\n+/)
    .flatMap((p) => {
      // If a paragraph is very long (>500 chars), try to split on sentences
      if (p.length > 500) {
        return p.split(/(?<=[.!?])\s+(?=[A-Z])/)
          .map((s) => s.trim())
          .filter((s) => s.length > 0);
      }
      return [p.trim()];
    })
    .filter((p) => p.length > 0);

  const seen = new Set();
  const deduped = [];

  for (const sentence of sentences) {
    // Normalize for comparison (lowercase, collapse whitespace)
    const normalized = sentence.toLowerCase().replace(/\s+/g, " ").trim();
    if (normalized.length < 3) continue;
    if (seen.has(normalized)) continue;

    // Also check if this sentence is a substring of an already-added sentence (or vice versa)
    let isDuplicate = false;
    for (const existing of seen) {
      if (existing.includes(normalized) || normalized.includes(existing)) {
        isDuplicate = true;
        break;
      }
    }
    if (isDuplicate) continue;

    seen.add(normalized);
    deduped.push(sentence);
  }

  const result = deduped.join("\n").trim();
  return result || null;
}

/**
 * Clean and normalize side effects into individual clean strings.
 *
 * Pipeline:
 *   1. Flatten input (handles arrays and newline-separated strings)
 *   2. Split compound effects (comma-separated, semicolon-separated)
 *   3. Strip HTML from each entry
 *   4. Remove noise prefixes
 *   5. Lowercase and trim
 *   6. Filter by length bounds
 *   7. Deduplicate
 *
 * @param {Array|string} rawSideEffects - Raw side effects data
 * @returns {string[]} - Clean array like ["nausea", "vomiting", "headache"]
 */
function cleanSideEffects(rawSideEffects) {
  if (!rawSideEffects) return [];

  // Normalize to flat array of strings
  const inputItems = Array.isArray(rawSideEffects) ? rawSideEffects : [rawSideEffects];

  const rawStrings = inputItems
    .flatMap((item) => {
      if (typeof item !== "string") return [];
      return item.split(/\n+/);
    })
    .filter(Boolean);

  // Process each string
  const allEffects = [];

  for (const raw of rawStrings) {
    let cleaned = stripHtml(raw);

    // Remove noise prefixes
    for (const pattern of SIDE_EFFECT_NOISE_PATTERNS) {
      cleaned = cleaned.replace(pattern, "");
    }

    // Split on commas, semicolons, "and" conjunctions, bullet separators
    const parts = cleaned
      .split(/[,;•·]|\band\b/)
      .map((part) => part.trim().toLowerCase())
      .filter(Boolean);

    allEffects.push(...parts);
  }

  // Clean, validate, deduplicate
  const seen = new Set();
  const results = [];

  for (const effect of allEffects) {
    // Remove trailing/leading punctuation and parentheses
    const trimmed = effect
      .replace(/^[\s\-*•.,:;()\[\]]+/, "")
      .replace(/[\s\-*•.,:;()\[\]]+$/, "")
      .trim();

    if (trimmed.length < SIDE_EFFECT_MIN_LENGTH) continue;
    if (trimmed.length > SIDE_EFFECT_MAX_LENGTH) continue;

    // Skip entries with too many words (likely sentence fragments, not individual effects)
    const wordCount = trimmed.split(/\s+/).length;
    if (wordCount > SIDE_EFFECT_MAX_WORDS) continue;

    // Skip generic/noise entries
    if (/^(common|rare|serious|some|the|these|this|may|can|include)/i.test(trimmed)) continue;
    if (/^side\s*effects?/i.test(trimmed)) continue;

    // Skip sentence fragments (contain verbs/phrases that indicate descriptive text, not side effects)
    if (/\b(belongs to|used to|works by|known as|called|prescribed|marketed by|which|that is|it is|refers to|contains|consisting)\b/i.test(trimmed)) continue;

    // Skip parenthetical fragments (orphan closing/opening parens)
    if (/^\)/.test(trimmed) || /\($/.test(trimmed)) continue;

    const normalized = trimmed.toLowerCase();
    if (seen.has(normalized)) continue;

    seen.add(normalized);
    results.push(normalized);
  }

  return results;
}

/**
 * Clean FAQ array.
 *
 * Pipeline:
 *   1. Strip HTML from questions and answers
 *   2. Deduplicate by normalized question text
 *   3. Remove empty/trivial entries
 *
 * @param {Array} rawFaq - Array of { question, answer } objects
 * @returns {Array} - Cleaned FAQ array
 */
function cleanFaq(rawFaq) {
  if (!Array.isArray(rawFaq)) return [];

  const seen = new Set();
  const results = [];

  for (const entry of rawFaq) {
    const question = stripHtml(entry?.question || "").trim();
    const answer = stripHtml(entry?.answer || "").trim();

    if (!question || !answer) continue;
    if (question.length < 5 || answer.length < 5) continue;

    const normalizedKey = question.toLowerCase().replace(/\s+/g, " ").trim();
    if (seen.has(normalizedKey)) continue;

    seen.add(normalizedKey);
    results.push({ question, answer });
  }

  return results;
}

module.exports = {
  stripHtml,
  cleanDescription,
  cleanSideEffects,
  cleanFaq,
};
