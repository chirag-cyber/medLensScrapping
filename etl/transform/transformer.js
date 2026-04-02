/**
 * ETL Transformer
 * Cleans and normalizes incoming raw JSON scraped data.
 */

const NOISE_WORDS = [
  "tablet", "tablets", "capsule", "capsules", "strip", "strips", 
  "bottle", "bottles", "syrup", "suspension", "injection", "drop",
  "drops", "ointment", "cream", "gel", "spray", "sachet", "box",
  "pack", "of", "gm", "ml", "mg", "kg", "mcg"
];

const DOSAGE_REGEX = /(\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|-?gm?|-?l|-?ml|-?mcg|-?mg))(?!\w)/i;

function cleanString(str) {
  if (!str) return "";
  return str.toLowerCase().trim().replace(/[^\w\s-]/g, " ").replace(/\s+/g, " ").trim();
}

function removeNoiseWords(str) {
  let words = str.split(" ");
  words = words.filter(word => !NOISE_WORDS.includes(word));
  return words.join(" ").trim();
}

function extractDosage(str) {
  if (!str) return null;
  const match = str.match(DOSAGE_REGEX);
  if (match) {
    return match[1].toLowerCase().replace(/\s/g, ""); // "650 mg" -> "650mg"
  }
  return null;
}

function normalizeSalt(salt) {
  if (!salt) return null;
  let cleanSalt = cleanString(salt);
  
  // Remove dosage from salt "paracetamol 650mg" -> "paracetamol"
  cleanSalt = cleanSalt.replace(DOSAGE_REGEX, "");
  
  // Clean up any double spaces left behind
  return removeNoiseWords(cleanSalt).replace(/\s+/g, " ").trim();
}

function normalizeName(name) {
  if (!name) return "";
  let cleanTitle = cleanString(name);
  
  // We remove the dosage from the normalized name for better grouping 
  // We also remove noise words
  cleanTitle = cleanTitle.replace(DOSAGE_REGEX, "");
  cleanTitle = removeNoiseWords(cleanTitle);

  return cleanTitle.trim();
}

function transformRecord(rawRecord) {
  // Input: { name, salt, price, source/platform, url }
  
  const dosage = extractDosage(rawRecord.name) || extractDosage(rawRecord.salt) || null;
  
  return {
    raw_name: rawRecord.name || "Unknown Product",
    normalized_name: normalizeName(rawRecord.name),
    raw_salt: rawRecord.salt || null,
    normalized_salt: normalizeSalt(rawRecord.salt),
    dosage: dosage,
    platform: (rawRecord.platform || rawRecord.source || "unknown").toLowerCase(),
    price: parseFloat(rawRecord.price),
    url: rawRecord.url
  };
}

module.exports = {
  transformRecord,
  normalizeName,
  normalizeSalt,
  extractDosage,
  removeNoiseWords
};
