/**
 * ETL Transformer
 * Cleans and normalizes incoming raw JSON scraped data.
 */

const { cleanMedicineName, isValidMedicineName } = require("./nameCleaner");
const { cleanDescription, cleanSideEffects, cleanFaq } = require("./sanitizer");

const NOISE_WORDS = [
  "tablet",
  "tablets",
  "capsule",
  "capsules",
  "strip",
  "strips",
  "bottle",
  "bottles",
  "syrup",
  "suspension",
  "injection",
  "drop",
  "drops",
  "ointment",
  "cream",
  "gel",
  "spray",
  "sachet",
  "box",
  "pack",
  "of",
  "gm",
  "ml",
  "mg",
  "kg",
  "mcg",
];

const DOSAGE_REGEX =
  /((?:\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)?\s*(?:\+|and|&|\/|-)?\s*)*\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|-?gm?|-?l|-?ml|-?mcg|-?mg))(?!\w)/i;
const STRICT_MANDATORY_FIELDS = ["name", "price", "name_quality"];
const PACK_UNIT_ALIASES = {
  tablet: "tablets",
  tablets: "tablets",
  tab: "tablets",
  tabs: "tablets",
  capsule: "capsules",
  capsules: "capsules",
  cap: "capsules",
  caps: "capsules",
  softgel: "softgels",
  softgels: "softgels",
  sachet: "sachets",
  sachets: "sachets",
  bottle: "bottles",
  bottles: "bottles",
  vial: "vials",
  vials: "vials",
  ampoule: "ampoules",
  ampoules: "ampoules",
  drop: "drops",
  drops: "drops",
  ml: "ml",
  l: "l",
  g: "g",
  gm: "g",
  kg: "kg",
};
const SALT_METADATA_SPLIT_REGEX =
  /\b(?:salt synonyms?|storage|store below|substitutes?|click here|view all|available with same salt composition|view available alternative|top combinations?|uses of|side effects of|how to use|introduction|which belongs to|belongs to|also known as|therapeutic|trusted active ingredient|as an active ingredient|active ingredient|ingredient|widely recognized|providing relief|medicines? called|prescribed for|used to|works by|marketed by|manufacturer|author details?|written by)\b/i;

function cleanString(str) {
  if (!str) return "";
  return str
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanText(str) {
  if (!str) return null;
  const normalized = String(str).replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  return normalized || null;
}

function normalizeSaltSourceText(text) {
  const cleaned = cleanText(text);
  if (!cleaned) return null;

  return cleaned
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1 $2")
    .replace(/(\d)([A-Z][a-z])/g, "$1 $2")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeHttpUrl(url) {
  const cleaned = cleanText(url);
  if (!cleaned) return null;

  try {
    const parsed = new URL(cleaned);
    if (!["http:", "https:"].includes(parsed.protocol)) return null;
    parsed.hash = "";
    return parsed.href;
  } catch {
    return null;
  }
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function removeNoiseWords(str) {
  let words = str.split(" ");
  words = words.filter((word) => !NOISE_WORDS.includes(word));
  return words.join(" ").trim();
}

function extractDosage(str, isRisky = false) {
  if (!str) return null;

  if (String(str).match(/^https?:\/\//i) || String(str).match(/^\/[a-z0-9-]+\//i)) {
    return null;
  }

  let cleanStr = String(str);
  
  cleanStr = cleanStr.replace(/\|\s*1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/-\s*1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/\bby\s+1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/\bat\s+1mg\b/gi, " ");
  cleanStr = cleanStr.replace(/\b1mg\s+platform\b/gi, " ");
  cleanStr = cleanStr.replace(/1mg\.com/gi, " ");

  const originalMatches = [...String(str).matchAll(new RegExp(DOSAGE_REGEX.source, "gi"))]
    .map(m => m[1].toLowerCase().replace(/\s/g, ""));
    
  if (originalMatches.includes("1mg")) {
     console.log(`[DOSAGE] '1mg' found in string: "${str}"`);
  }

  const matches = [...cleanStr.matchAll(new RegExp(DOSAGE_REGEX.source, "gi"))]
    .map((match) => normalizeDosageToken(match[1]))
    .filter(Boolean);

  if (matches.length === 0) return null;

  matches.sort((a, b) => {
      const aPlus = (a.match(/\+/g) || []).length;
      const bPlus = (b.match(/\+/g) || []).length;
      if (aPlus !== bPlus) return bPlus - aPlus;
      
      const aMag = parseFloat(a) || 0;
      const bMag = parseFloat(b) || 0;
      return bMag - aMag;
  });

  const extracted = matches[0];

  // If extraction is considered risky (e.g. from description) and it's exactly 1mg, 
  // be cautious if it resembles a platform mention that bypassed pre-filters.
  // But generally, we trust pre-filters to catch platform noise.
  if (isRisky && extracted === "1mg" && String(str).toLowerCase().includes("platform")) {
     return null;
  }

  return extracted;
}

function normalizePackUnit(unit) {
  return PACK_UNIT_ALIASES[String(unit || "").toLowerCase()] || null;
}

function detectPackUnit(text) {
  const normalized = cleanText(text);
  if (!normalized) return null;

  const match = normalized.match(
    /\b(tablets?|tabs?|capsules?|caps?|softgels?|sachets?|bottles?|vials?|ampoules?|drops?)\b/i
  );
  return normalizePackUnit(match?.[1] || "");
}

function normalizePackSizeValue(value) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) return null;
  return Number.isInteger(parsed) ? String(parsed) : String(parsed);
}

function buildPackSize(value, unit) {
  const normalizedValue = normalizePackSizeValue(value);
  const normalizedUnit = normalizePackUnit(unit);
  if (!normalizedValue || !normalizedUnit) return null;
  return `${normalizedValue}-${normalizedUnit}`;
}

function extractPackSize(quantityText, nameText) {
  const normalizedQuantity = cleanText(quantityText);
  const normalizedName = cleanText(nameText);

  if (normalizedQuantity) {
    const directQuantityMatch = normalizedQuantity.match(
      /(\d+(?:\.\d+)?)\s*(tablets?|tabs?|capsules?|caps?|softgels?|sachets?|bottles?|vials?|ampoules?|drops?|ml|l|gm|g|kg)\b/i
    );
    if (directQuantityMatch) {
      return buildPackSize(directQuantityMatch[1], directQuantityMatch[2]);
    }
  }

  const combinedText = [normalizedQuantity, normalizedName].filter(Boolean).join(" ");
  const stripMatch = combinedText.match(/strip of (\d+(?:\.\d+)?)/i);
  if (stripMatch) {
    return buildPackSize(stripMatch[1], detectPackUnit(combinedText) || "tablets");
  }

  if (normalizedName) {
    const shorthandMatch = normalizedName.match(/\b(\d+(?:\.\d+)?)\s*'?s\b/i);
    if (shorthandMatch) {
      return buildPackSize(shorthandMatch[1], detectPackUnit(normalizedName) || "tablets");
    }

    const liquidMatch = normalizedName.match(/(\d+(?:\.\d+)?)\s*(ml|l|gm|g|kg)\b/i);
    if (liquidMatch) {
      return buildPackSize(liquidMatch[1], liquidMatch[2]);
    }
  }

  return null;
}

function trimSaltMetadata(salt) {
  const cleaned = normalizeSaltSourceText(salt);
  if (!cleaned) return null;

  const parts = cleaned.split(SALT_METADATA_SPLIT_REGEX);
  const candidate = cleanText(parts[0]);
  if (!candidate) return null;

  return candidate
    .replace(/\b(?:therapeutic|medicine|medicines|drug|drugs)\b.*$/i, "")
    .replace(/[|;:].*$/g, "")
    .replace(/,\s*(?:a|an)\b.*$/i, "")
    .replace(/\s+/g, " ")
    .trim() || null;
}

function normalizeSaltComponent(component) {
  if (!component) return null;

  const source = normalizeSaltSourceText(component);
  if (!source) return null;

  const normalized = cleanString(source)
    .replace(new RegExp(DOSAGE_REGEX.source, "gi"), " ")
    .replace(/\b\d+(?:\.\d+)?\s*\/\s*\d+(?:\.\d+)?\b/g, " ")
    .replace(/\b(?:ip|bp|usp|eq|equivalent|strength|composition|salt|salts|synonyms|store|below|top|combinations|available|same|click|view|all|substitutes|sub)\b/g, " ")
    .replace(/\b(?:which|belongs|known|therapeutic|trusted|ingredient|widely|recognized|providing|relief|medicines|called|prescribed|used|works|marketed|manufacturer)\b/g, " ")
    .replace(/\b(?:of|to|for|the|group|as|also)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  return normalized || null;
}

function extractSaltTokens(salt) {
  const trimmedSalt = trimSaltMetadata(salt);
  if (!trimmedSalt) return [];

  const parts = trimmedSalt
    .replace(/[()]/g, " ")
    .split(/\s*(?:\/|\+|,|&| along with | with | and )\s*/i)
    .map((part) => normalizeSaltComponent(part))
    .filter(Boolean);

  return [...new Set(parts)];
}

function buildSaltSignature(saltTokens) {
  if (!Array.isArray(saltTokens) || saltTokens.length === 0) return null;
  return [...new Set(saltTokens)].sort().join("+");
}

function removePackSizeTokens(name, packSize) {
  if (!name) return "";
  let normalized = name;

  if (!packSize) return normalized;

  const [value, unit] = String(packSize).split("-");
  if (!value || !unit) return normalized;

  const unitVariants =
    unit === "tablets"
      ? "tablets?|tabs?|tab"
      : unit === "capsules"
        ? "capsules?|caps?|cap"
        : escapeRegExp(unit);

  normalized = normalized.replace(
    new RegExp(`\\bstrip\\s+of\\s+${escapeRegExp(value)}\\b`, "gi"),
    " "
  );
  normalized = normalized.replace(
    new RegExp(`\\b${escapeRegExp(value)}\\s*(?:${unitVariants})\\b`, "gi"),
    " "
  );
  normalized = normalized.replace(
    new RegExp(`\\b${escapeRegExp(value)}\\s*s\\b`, "gi"),
    " "
  );

  return normalized;
}

function normalizeSalt(salt) {
  const trimmedSalt = trimSaltMetadata(salt);
  if (!trimmedSalt) return null;

  let cleanSalt = cleanString(trimmedSalt)
    .replace(/-+/g, " ")
    .replace(/\b\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg)\s+of\b/gi, " ")
    .replace(/\([^)]*\)/g, " ");
  cleanSalt = cleanSalt.replace(new RegExp(DOSAGE_REGEX.source, "gi"), " ");
  cleanSalt = cleanSalt.replace(/\b(?:therapeutic|which|belongs|known|trusted|ingredient|widely|recognized|providing|relief|medicines|called|prescribed|used|works|marketed|manufacturer)\b.*$/gi, " ");
  const normalized = removeNoiseWords(cleanSalt)
    .replace(/\b(?:of|to|for|the|group|called|as)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return normalized || null;
}

function shouldUseQueryHintAsSalt(rawName, normalizedQueryHint) {
  if (!rawName || !normalizedQueryHint) return false;
  return cleanString(rawName).includes(normalizedQueryHint);
}

function normalizeDosageToken(dosage) {
  const cleaned = cleanText(dosage);
  if (!cleaned) return null;
  
  let m = cleaned.toLowerCase();
  const parts = [...m.matchAll(/(\d+(?:\.\d+)?)\s*(mg|ml|mcg|g|gm|kg|%)?/gi)];
  
  if (parts.length <= 1) {
      return m.replace(/\s+/g, "");
  }
  
  let finalParts = [];
  let lastUnit = "mg"; 
  for (let i = parts.length - 1; i >= 0; i--) {
     if (parts[i][2]) {
         lastUnit = parts[i][2].toLowerCase();
     }
     finalParts.unshift(parts[i][1] + lastUnit);
  }
  return finalParts.join("+");
}

function normalizeName(name, options = {}) {
  if (!name) return "";
  const { dosage = null, packSize = null } = options;

  let cleanTitle = cleanString(name);
  const normalizedDosage = normalizeDosageToken(dosage);

  if (normalizedDosage) {
    cleanTitle = cleanTitle.replace(
      new RegExp(DOSAGE_REGEX.source, "gi"),
      ` ${normalizedDosage} `
    );

    const numericPart = normalizedDosage.match(/\d+(?:\.\d+)?/)?.[0];
    if (numericPart) {
      cleanTitle = cleanTitle.replace(
        new RegExp(`\\b${escapeRegExp(numericPart)}\\b`, "gi"),
        ` ${normalizedDosage} `
      );
    }
  } else {
    cleanTitle = cleanTitle.replace(new RegExp(DOSAGE_REGEX.source, "gi"), (match) => {
      const normalizedMatch = normalizeDosageToken(match);
      return normalizedMatch ? ` ${normalizedMatch} ` : " ";
    });
  }

  cleanTitle = removePackSizeTokens(cleanTitle, packSize);
  cleanTitle = removeNoiseWords(cleanTitle);
  cleanTitle = cleanTitle.replace(/\bnew\b/g, " ").replace(/\s+/g, " ").trim();
  return cleanTitle.trim();
}

function normalizeList(items) {
  if (!items) return [];

  const normalizedItems = (Array.isArray(items) ? items : [items])
    .flatMap((item) => {
      if (typeof item === "string") return item.split(/\n+/);
      return [];
    })
    .map((item) => cleanText(item))
    .filter(Boolean);

  return [...new Set(normalizedItems)];
}

function normalizeFaq(faqItems) {
  if (!Array.isArray(faqItems)) return [];

  const seen = new Set();
  const normalizedFaq = [];

  faqItems.forEach((entry) => {
    const question = cleanText(entry?.question);
    const answer = cleanText(entry?.answer);
    if (!question || !answer) return;

    const key = `${question.toLowerCase()}::${answer.toLowerCase()}`;
    if (seen.has(key)) return;

    seen.add(key);
    normalizedFaq.push({ question, answer });
  });

  return normalizedFaq;
}

function buildCanonicalKey({
  normalized_name,
  normalized_salt,
  dosage,
}) {
  return buildNameMatchKey(normalized_name, dosage);
}

function buildNameMatchKey(normalizedName, dosage) {
  const cleaned = cleanText(normalizedName);
  const cleanedDosage = normalizeDosageToken(dosage);
  if (!cleaned) return null;
  if (cleanedDosage) {
    return `name:${cleaned}|dose:${cleanedDosage}`;
  }
  return `name:${cleaned}`;
}

function buildSaltDosageMatchKey(normalizedSalt, dosage) {
  const cleanedSalt = cleanText(normalizedSalt);
  const cleanedDosage = normalizeDosageToken(dosage);

  if (!cleanedSalt || !cleanedDosage) return null;
  return `salt:${cleanedSalt}|dose:${cleanedDosage}`;
}

function buildMatchKeys({ normalized_name, normalized_salt, dosage }) {
  return [
    ...new Set(
      [
        buildNameMatchKey(normalized_name, dosage),
      ].filter(Boolean)
    ),
  ];
}

function getMissingDetailFields(transformedRecord) {
  const missing = [];

  if (!transformedRecord.raw_name || transformedRecord.raw_name === "Unknown Product") {
    missing.push("name");
  }
  // Validate name quality (SEO contamination, length bounds)
  if (transformedRecord.cleaned_name === null && transformedRecord.raw_name && transformedRecord.raw_name !== "Unknown Product") {
    missing.push("name_quality");
  }
  if (!transformedRecord.raw_salt) missing.push("salt");
  if (!transformedRecord.description) missing.push("description");
  if (!transformedRecord.dosage) missing.push("dosage");
  // sideEffects is optional — kept wherever available but not mandatory
  if (
    transformedRecord.source_type === "product" &&
    (typeof transformedRecord.price !== "number" || Number.isNaN(transformedRecord.price))
  ) {
    missing.push("price");
  }

  return missing;
}

function isStrictlyCompleteRecord(transformedRecord) {
  return getMissingDetailFields(transformedRecord).length === 0;
}

function transformRecord(rawRecord) {
  const rawName = cleanText(rawRecord.name) || "Unknown Product";
  const queryHintSalt = trimSaltMetadata(rawRecord.queryHint);
  const normalizedQueryHintSalt = normalizeSalt(queryHintSalt);
  const rawSaltCandidate = trimSaltMetadata(rawRecord.salt);
  const fallbackSaltFromQuery = shouldUseQueryHintAsSalt(rawName, normalizedQueryHintSalt)
    ? queryHintSalt
    : null;
  const rawSalt = rawSaltCandidate || fallbackSaltFromQuery;
  const rawDescription = cleanText(rawRecord.description);
  const platformValue = cleanText(rawRecord.platform || rawRecord.source || "unknown");
  const normalizedSalt = normalizeSalt(rawSalt) || normalizedQueryHintSalt;
  const saltTokens = extractSaltTokens(rawSalt);
  const primarySaltKey = normalizedSalt || saltTokens[0] || null;
  // ─── STRICT NAME CLEANING ──────────────────────────────────────
  // Apply SEO keyword removal, special char stripping, length validation early
  // so platform suffixes (like " | 1mg") don't corrupt dosage extraction.
  const cleanedName = cleanMedicineName(rawName);

  let dosage = extractDosage(rawRecord.dosage);
  if (!dosage) {
    dosage = extractDosage(cleanedName || rawName);
  }
  if (!dosage) {
    dosage = extractDosage(rawSalt, true);
    if (dosage) console.log(`[DOSAGE] Fallback triggered: extracted ${dosage} from salt for "${rawName}"`);
  }
  if (!dosage) {
    dosage = extractDosage(rawDescription, true);
    if (dosage) console.log(`[DOSAGE] Fallback triggered: extracted ${dosage} from description for "${rawName}"`);
  }

  if (dosage && dosage.includes("+")) {
    console.log(`[DOSAGE] Successful corrected extraction (multi-dosage): ${dosage} for "${rawName}"`);
  }
  const packSize = extractPackSize(rawRecord.quantity, cleanedName || rawName);
  const normalizedName = normalizeName(cleanedName || rawName, {
    dosage,
    packSize,
  });
  const nameMatchKey = buildNameMatchKey(normalizedName);
  const saltDosageKey = buildSaltDosageMatchKey(normalizedSalt, dosage);

  // ─── DESCRIPTION CLEANING ──────────────────────────────────────
  // Strip HTML, remove promotional text, deduplicate paragraphs
  const description = cleanDescription(rawDescription);

  // ─── SIDE EFFECTS CLEANING ─────────────────────────────────────
  // Normalize into clean individual strings like ["nausea", "vomiting", "headache"]
  const rawSideEffects = normalizeList(rawRecord.sideEffects || rawRecord.side_effects);
  const sideEffects = cleanSideEffects(rawSideEffects);

  // ─── FAQ CLEANING ──────────────────────────────────────────────
  // Strip HTML from Q&A, deduplicate by normalized question
  const rawFaq = normalizeFaq(rawRecord.faq);
  const faq = cleanFaq(rawFaq);

  const priceValue =
    rawRecord.price === null || rawRecord.price === undefined || rawRecord.price === ""
      ? null
      : Number.parseFloat(rawRecord.price);

  const transformedRecord = {
    canonical_key: buildCanonicalKey({
      normalized_name: normalizedName,
      normalized_salt: normalizedSalt,
      dosage,
    }),
    name_match_key: nameMatchKey,
    salt_dosage_key: saltDosageKey,
    match_keys: buildMatchKeys({
      normalized_name: normalizedName,
      normalized_salt: normalizedSalt,
      dosage,
    }),
    raw_name: rawName,
    cleaned_name: cleanedName,
    normalized_name: normalizedName,
    raw_salt: rawSalt,
    normalized_salt: normalizedSalt,
    primary_salt_key: primarySaltKey,
    salt_tokens: saltTokens,
    pack_size: packSize,
    dosage,
    platform: platformValue ? platformValue.toLowerCase() : "unknown",
    source_type: cleanString(rawRecord.sourceType || rawRecord.source_type || "product"),
    price: Number.isNaN(priceValue) ? null : priceValue,
    image_url: normalizeHttpUrl(rawRecord.image_url || rawRecord.image),
    manufacturer: cleanText(rawRecord.manufacturer || rawRecord.brand),
    description,
    side_effects: sideEffects,
    faq,
    url: cleanText(rawRecord.url),
    raw_payload: rawRecord,
  };

  transformedRecord.missing_detail_fields = getMissingDetailFields(transformedRecord);

  return transformedRecord;
}

module.exports = {
  STRICT_MANDATORY_FIELDS,
  buildCanonicalKey,
  buildMatchKeys,
  buildNameMatchKey,
  buildSaltDosageMatchKey,
  cleanText,
  extractDosage,
  getMissingDetailFields,
  isStrictlyCompleteRecord,
  extractPackSize,
  extractSaltTokens,
  normalizeHttpUrl,
  normalizeFaq,
  normalizeList,
  normalizeName,
  normalizeSalt,
  removeNoiseWords,
  transformRecord,
  // Re-export from submodules for external use
  cleanMedicineName,
  isValidMedicineName,
};
