/**
 * ETL Transformer
 * Cleans and normalizes incoming raw JSON scraped data.
 */

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
  /(\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|-?gm?|-?l|-?ml|-?mcg|-?mg))(?!\w)/i;
const STRICT_MANDATORY_FIELDS = ["name", "price", "salt", "description", "dosage", "sideEffects"];
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
  /\b(?:salt synonyms?|storage|store below|substitutes?|click here|view all|available with same salt composition|view available alternative|top combinations?|uses of|side effects of|how to use|introduction|which belongs to|belongs to|also known as|therapeutic|trusted active ingredient|widely recognized|providing relief|medicines? called|prescribed for|used to|works by|marketed by|manufacturer|author details?|written by)\b/i;

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

function extractDosage(str) {
  if (!str) return null;
  const matches = [...String(str).matchAll(new RegExp(DOSAGE_REGEX.source, "gi"))]
    .map((match) => match[1].toLowerCase().replace(/\s/g, ""))
    .filter(Boolean);

  if (matches.length === 0) return null;

  const preferredMatches = matches.filter((match) => match !== "1mg");
  const candidates = preferredMatches.length > 0 ? preferredMatches : matches;

  const getMagnitude = (value) => {
    const numeric = Number.parseFloat(value);
    return Number.isFinite(numeric) ? numeric : 0;
  };

  return [...new Set(candidates)].sort((left, right) => getMagnitude(right) - getMagnitude(left))[0];
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
  return cleaned ? cleaned.toLowerCase().replace(/\s+/g, "") : null;
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
  const saltDosageKey = buildSaltDosageMatchKey(normalized_salt, dosage);
  if (saltDosageKey) return saltDosageKey;

  return buildNameMatchKey(normalized_name);
}

function buildNameMatchKey(normalizedName) {
  const cleaned = cleanText(normalizedName);
  return cleaned ? `name:${cleaned}` : null;
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
        buildNameMatchKey(normalized_name),
        buildSaltDosageMatchKey(normalized_salt, dosage),
      ].filter(Boolean)
    ),
  ];
}

function getMissingDetailFields(transformedRecord) {
  const missing = [];

  if (!transformedRecord.raw_name || transformedRecord.raw_name === "Unknown Product") {
    missing.push("name");
  }
  if (!transformedRecord.raw_salt) missing.push("salt");
  if (!transformedRecord.description) missing.push("description");
  if (!transformedRecord.dosage) missing.push("dosage");
  if (!Array.isArray(transformedRecord.side_effects) || transformedRecord.side_effects.length === 0) {
    missing.push("sideEffects");
  }
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
  const description = cleanText(rawRecord.description);
  const platformValue = cleanText(rawRecord.platform || rawRecord.source || "unknown");
  const normalizedSalt = normalizeSalt(rawSalt) || normalizedQueryHintSalt;
  const saltTokens = extractSaltTokens(rawSalt);
  const primarySaltKey = normalizedSalt || saltTokens[0] || null;
  const dosage =
    extractDosage(rawRecord.dosage) ||
    extractDosage(rawName) ||
    extractDosage(rawSalt) ||
    extractDosage(description) ||
    null;
  const packSize = extractPackSize(rawRecord.quantity, rawName);
  const normalizedName = normalizeName(rawName, {
    dosage,
    packSize,
  });
  const nameMatchKey = buildNameMatchKey(normalizedName);
  const saltDosageKey = buildSaltDosageMatchKey(normalizedSalt, dosage);

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
    side_effects: normalizeList(rawRecord.sideEffects || rawRecord.side_effects),
    faq: normalizeFaq(rawRecord.faq),
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
};
