/**
 * Medicine Name Cleaner
 *
 * Strict cleaning rules:
 *   - The "name" field must ONLY contain: brand name, salt name, dosage
 *   - All SEO / marketing / informational keywords and everything after them are removed
 *   - Special characters are stripped
 *   - Whitespace is normalized
 *   - Result must be 3–60 characters or it is rejected
 */

// ─── SEO / marketing keywords to truncate at ────────────────────────
// Everything at and after the first match is removed.
// Order doesn't matter — we find the earliest match position.
const SEO_TRUNCATION_PHRASES = [
  // Direct spec requirements
  "price",
  "uses",
  "side effects",
  "side-effects",
  "sideeffects",
  "composition",
  "buy",
  "online",
  "review",
  "reviews",
  "benefits",
  // Extended SEO / pharma noise
  "substitute",
  "substitutes",
  "dosage",
  "interaction",
  "interactions",
  "warning",
  "warnings",
  "precaution",
  "precautions",
  "storage",
  "about",
  "how to use",
  "how it works",
  "how does it work",
  "safety advice",
  "expert advice",
  "manufacturer",
  "marketed by",
  "faq",
  "frequently asked",
  "medicine information",
  "drug information",
  "medicine details",
  "drug details",
  "in hindi",
  "in india",
  "best price",
  "lowest price",
  "order now",
  "order online",
  "sale",
  "discount",
  "cash on delivery",
  "cod",
  "free shipping",
  "free delivery",
  "available",
  "availability",
  "compare",
  "alternative",
  "alternatives",
  "generic",
  "information",
  "overview",
  "introduction",
  "view all",
  "know more",
  "read more",
  "click here",
];

// Build a single regex that matches the earliest SEO phrase (case-insensitive).
// We anchor on word boundaries where possible and escape regex chars.
function buildSeoTruncationRegex() {
  const escaped = SEO_TRUNCATION_PHRASES
    .sort((a, b) => b.length - a.length) // longest first for greedy matching
    .map((phrase) => phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

  // Match: optional leading separator (comma, pipe, dash, parens) then the phrase
  return new RegExp(
    `(?:[,|\\-–—(]\\s*)?\\b(?:${escaped.join("|")})\\b`,
    "i"
  );
}

const SEO_TRUNCATION_REGEX = buildSeoTruncationRegex();

// Phrases that, if present after cleaning, indicate a bad/noisy name
const SEO_REJECT_PHRASES = [
  /\bprice\b/i,
  /\buses\b/i,
  /\bside\s*effects?\b/i,
  /\bcomposition\b/i,
  /\bbuy\b/i,
  /\bonline\b/i,
  /\breview\b/i,
  /\bbenefits?\b/i,
  /\bsubstitute\b/i,
  /\bdosage\b/i,
  /\binteraction\b/i,
  /\bwarning\b/i,
  /\bprecaution\b/i,
  /\bhow to use\b/i,
  /\bsafety advice\b/i,
  /\bexpert advice\b/i,
  /\border\s*(now|online)\b/i,
  /\bfree\s*(shipping|delivery)\b/i,
  /\bbest\s*price\b/i,
  /\blowest\s*price\b/i,
  /\bcash on delivery\b/i,
  /\bfaq\b/i,
  /\bfrequently asked\b/i,
  /\binformation\b/i,
  /\boverview\b/i,
];

// ─── Name length bounds ─────────────────────────────────────────────
const NAME_MIN_LENGTH = 3;
const NAME_MAX_LENGTH = 60;

/**
 * Decode HTML entities commonly found in scraped medicine names.
 * e.g. &#39; → ', &amp; → &, &#x27; → '
 */
function decodeHtmlEntities(text) {
  if (!text) return text;
  return text
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/gi, "'")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(parseInt(code, 10)))
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

// Pack-size patterns to remove from cleaned names
// e.g. "10's", "strip of 10", "15 tablets", "bottle of 100ml"
const PACK_SIZE_PATTERNS = [
  /\bstrip\s+of\s+\d+\b/gi,
  /\bpack\s+of\s+\d+\b/gi,
  /\bbottle\s+of\s+\d+\s*(?:ml|l)?\b/gi,
  /\b\d+(?:\.\d+)?\s*(?:'s)\b/gi, // 10's
  // specifically look for exact phrases like "10 tablets" where it's likely a pack size,
  // but exclude big numbers which are likely dosages (e.g. 500 Tablet)
  /\b(?:1|2|3|4|5|6|7|8|9|10|12|14|15|20|30|50|60|100)\s+(?:tablets?|capsules?|strips?)\b/gi,
];

/**
 * Clean a raw medicine name by removing SEO/marketing text.
 *
 * Pipeline:
 *   1. Decode HTML entities (&#39; → ')
 *   2. Convert to lowercase
 *   3. Find earliest SEO keyword and truncate everything from that point
 *   4. Remove pack-size patterns (10's, strip of 10, 15 tablets)
 *   5. Remove special characters (keep alphanumeric, spaces, hyphens)
 *   6. Normalize whitespace
 *   7. Trim
 *
 * @param {string} rawName - The raw scraped medicine name
 * @returns {string|null} - Cleaned name, or null if invalid
 */
function cleanMedicineName(rawName) {
  if (!rawName || typeof rawName !== "string") return null;

  // Step 1: decode HTML entities FIRST (e.g. &#39; → ')
  let name = decodeHtmlEntities(rawName);

  // Step 2: lowercase
  name = name.toLowerCase().trim();

  // Step 3: truncate at earliest SEO phrase
  const seoMatch = name.match(SEO_TRUNCATION_REGEX);
  if (seoMatch) {
    name = name.substring(0, seoMatch.index);
  }

  // Step 4: remove pack-size patterns (must happen before special char removal)
  for (const pattern of PACK_SIZE_PATTERNS) {
    name = name.replace(pattern, " ");
  }

  // Step 5: remove special characters (keep letters, digits, spaces, hyphens, periods)
  name = name.replace(/[^a-z0-9\s\-.']/g, " ");

  // Step 6: normalize whitespace
  name = name.replace(/\s+/g, " ").trim();

  // Step 7: remove trailing punctuation artifacts
  name = name.replace(/[\s\-,.|:]+$/, "").trim();

  // Validate
  if (!isValidMedicineName(name)) return null;

  return name;
}

/**
 * Validate a cleaned medicine name.
 *
 * Rules:
 *   - Must be between 3–60 characters
 *   - Must not contain any SEO rejection phrases
 *   - Must contain at least one letter
 *
 * @param {string} name - The cleaned name to validate
 * @returns {boolean}
 */
function isValidMedicineName(name) {
  if (!name || typeof name !== "string") return false;

  // Length check
  if (name.length < NAME_MIN_LENGTH || name.length > NAME_MAX_LENGTH) return false;

  // Must contain at least one alphabetic character
  if (!/[a-z]/i.test(name)) return false;

  // Reject if any SEO phrase is still present
  if (SEO_REJECT_PHRASES.some((pattern) => pattern.test(name))) return false;

  return true;
}

module.exports = {
  cleanMedicineName,
  isValidMedicineName,
  NAME_MIN_LENGTH,
  NAME_MAX_LENGTH,
  SEO_TRUNCATION_PHRASES,
};
