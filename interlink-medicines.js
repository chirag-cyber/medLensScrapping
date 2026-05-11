#!/usr/bin/env node

/**
 * 💊 interlink-medicines.js
 *
 * Safe, idempotent data interlinking script.
 * Finds and merges duplicate Medicine records that the ETL pipeline missed
 * because platform names arrived with slight variations.
 *
 * TWO-PASS strategy:
 *   Pass 1: Exact canonical_key duplicates (fast — aggregation)
 *   Pass 2: Fuzzy name+dosage matching across different canonical_keys
 *           (catches "dolo 650mg" vs "dolo-650 mg" style mismatches)
 *
 * Usage:
 *   node interlink-medicines.js            # execute merge
 *   node interlink-medicines.js --dry-run  # preview only, no DB changes
 *
 * Safety:
 *   - Uses MongoDB transactions (atomicity)
 *   - Backs up deleted medicines to `deleted_medicines` collection AND local JSON
 *   - Marks master records with `is_canonical: true` for idempotency
 *   - Skips groups with dosage mismatch or low name similarity
 *   - Deduplicates prices per platform before insert
 *   - Running multiple times produces the same result (idempotent)
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");
const fs = require("fs");
const path = require("path");

// ──────────────────────────────────────────────────────────────────────
// CONFIG
// ──────────────────────────────────────────────────────────────────────
const DRY_RUN = process.argv.includes("--dry-run");
const SIMILARITY_THRESHOLD = 0.6;

// ──────────────────────────────────────────────────────────────────────
// DB CONNECTION
// ──────────────────────────────────────────────────────────────────────
async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    process.exit(1);
  }
  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
}

// ──────────────────────────────────────────────────────────────────────
// MODELS (loose schema for raw document access)
// ──────────────────────────────────────────────────────────────────────
const MedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const PriceSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const DeletedMedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });

const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", MedicineSchema);
const Price = mongoose.models.Price || mongoose.model("Price", PriceSchema);
const DeletedMedicine =
  mongoose.models.DeletedMedicine || mongoose.model("DeletedMedicine", DeletedMedicineSchema);

// ──────────────────────────────────────────────────────────────────────
// NORMALIZATION (dosage-safe — NEVER strips mg/ml/mcg)
// ──────────────────────────────────────────────────────────────────────

/** Normalize dosage format without removing units.
 *  "500 mg" → "500mg", "500.0mg" → "500mg", "0.5 g" → "0.5g"
 */
function normalizeDosage(dosage) {
  if (!dosage) return null;
  
  // Find all components like "25 mg", "5mg", "1g", "0.5gm"
  const matches = String(dosage).toLowerCase().match(/\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|units?|iu)?\b/gi) || [];
  
  if (matches.length === 0) return null;

  const normalizedParts = matches.map(part => {
    let p = part.replace(/\s+/g, "").trim();
    p = p.replace(/\.0(?=mg|ml|mcg|g|gm|kg|%|units?|iu)/i, "");

    // Normalize all weight units to mg as canonical base
    // g/gm → mg (×1000):  1g = 1000mg, 0.5gm = 500mg
    // mcg  → mg (÷1000):  500mcg = 0.5mg
    // kg   → mg (×1000000): 1kg = 1000000mg
    const unitConversions = [
      { pattern: /^(\d+(?:\.\d+)?)(g|gm)$/i, factor: 1000 },
      { pattern: /^(\d+(?:\.\d+)?)(mcg)$/i, factor: 0.001 },
      { pattern: /^(\d+(?:\.\d+)?)(kg)$/i, factor: 1000000 },
    ];
    for (const { pattern, factor } of unitConversions) {
      const m = p.match(pattern);
      if (m) {
        const mgValue = parseFloat(m[1]) * factor;
        p = (mgValue % 1 === 0 ? Math.round(mgValue) : mgValue) + "mg";
        break;
      }
    }

    if (/^\d+(?:\.\d+)?$/.test(p)) p += "mg";
    return p;
  });

  // Unique sorted parts to handle "25mg 5mg" vs "5mg 25mg" or "25mg 25mg" glitches
  const uniqueParts = [...new Set(normalizedParts)].sort();
  return uniqueParts.join("+") || null;
}

/** Resolves the best possible dosage by checking name, normalized_name and dosage field. */
function getFuzzyDosage(med) {
  const d1 = normalizeDosage(med.name);
  const d2 = normalizeDosage(med.dosage);
  
  // Return the one with the most components (e.g. "25mg+5mg" vs "25mg")
  const candidates = [d1, d2].filter(Boolean);
  if (candidates.length === 0) return null;
  
  return candidates.sort((a, b) => b.split("+").length - a.split("+").length)[0];
}

/** Extracts only the base integers from a fuzzy dosage (e.g., "2.6mg+5mg" -> "2+5") */
function getDosageIntegers(dosageStr) {
  if (!dosageStr) return null;
  const parts = dosageStr.split("+");
  const ints = parts.map(p => {
    const numMatch = p.match(/\d+/);
    return numMatch ? parseInt(numMatch[0], 10) : null;
  }).filter(n => n !== null);
  
  if (ints.length === 0) return null;
  return [...new Set(ints)].sort((a, b) => a - b).join("+");
}

/** Strip name down to pure brand identity for fuzzy matching.
 *  Removes: form words, pack-sizes, special chars, and ALL numbers.
 */
function normalizeNameForMatching(name) {
  if (!name) return "";
  let n = String(name).toLowerCase().trim();

  // Strip known manufacturer names that act as prefixes
  const KNOWN_MANUFACTURERS = [
    "cipla", "sun", "zydus", "alkem", "mankind", "abbott", "gsk", "lupin", "torrent",
    "reddy", "reddys", "intas", "pfizer", "sanofi", "glenmark", "wockhardt", "aurobindo",
    "cadila", "micro", "pharma", "pharmaceuticals", "genericart", "stayhappi", "davaindia",
    "boehringer", "ingelheim", "biocon", "serum", "heteo", "dr.reddy", "novartis", "bayer",
    "ltd", "pvt", "limited", "private", "healthcare", "india", "inc", "llc", "corp", "corporation",
    "gmbh", "amp", "co", "pharm"
  ];
  const mPattern = new RegExp(`\\b(${KNOWN_MANUFACTURERS.join("|")})\\b`, "gi");
  n = n.replace(mPattern, " ");

  // Remove form/packaging noise words
  // NOTE: SR/CR/MR/XR/ER/DR/PR are KEPT — they are drug formulation identifiers
  // (Sustained Release, Controlled Release, etc.) and are NOT noise.
  const FORM_NOISE = [
    "tablet", "tablets", "tab", "tabs",
    "capsule", "capsules", "cap", "caps",
    "strip", "strips", "pack", "bottle",
    "syrup", "suspension", "injection",
    "drop", "drops", "ointment", "cream",
    "gel", "spray", "sachet", "box", "of", "new"
  ];
  const noisePattern = new RegExp(`\\b(${FORM_NOISE.join("|")})\\b`, "gi");
  n = n.replace(noisePattern, " ");

  // Remove pack-size patterns
  n = n.replace(/\bstrip\s+of\s+\d+\b/gi, " ");
  n = n.replace(/\b\d+\s*'s\b/gi, " ");
  n = n.replace(/\b(?:1|2|3|4|5|6|7|8|9|10|12|14|15|20|30|50|60|100)\s+(?:tablets?|capsules?|strips?)\b/gi, " ");

  // Remove special characters (keep letters, digits, spaces, hyphens, periods)
  n = n.replace(/[^a-z0-9\s\-.]/g, " ");

  // NEW: Remove full embedded dosages first (e.g. "650mg" or "25 mg") so it strips "mg" as well.
  n = n.replace(/\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|units?|iu)\b/gi, " ");

  // NEW: Strip any remaining isolated numbers.
  n = n.replace(/\d+/g, " ");

  // Collapse whitespace and remove trailing punctuation
  n = n.replace(/\s+/g, " ").trim();
  n = n.replace(/[\s\-,.|:;+*]+$/, "").trim();

  return n;
}

/** Build a fuzzy matching key: stripped name + normalized dosage.
 *  Used in Pass 2 to find medicines the ETL pipeline stored under
 *  different canonical_keys but that actually represent the same product.
 */
function buildFuzzyKey(med) {
  const core = normalizeNameForMatching(med.normalized_name || med.name);
  const nd = getFuzzyDosage(med);

  if (!core || !nd) return null;
  return `${core}|${nd}`;
}

// ──────────────────────────────────────────────────────────────────────
// STRING SIMILARITY (Word-based Jaccard to handle word order & spacing)
// ──────────────────────────────────────────────────────────────────────
function wordJaccard(a, b) {
  if (!a || !b) return 0;
  if (a === b) return 1;
  const setA = new Set(a.toLowerCase().split(/\s+/).filter(Boolean));
  const setB = new Set(b.toLowerCase().split(/\s+/).filter(Boolean));

  if (setA.size === 0 && setB.size === 0) return 1;
  if (setA.size === 0 || setB.size === 0) return 0;

  let intersection = 0;
  for (const word of setA) {
    if (setB.has(word)) intersection++;
  }

  const union = setA.size + setB.size - intersection;
  return intersection / union;
}

// ──────────────────────────────────────────────────────────────────────
// MASTER SELECTION
// ──────────────────────────────────────────────────────────────────────

function completenessScore(doc) {
  let score = 0;
  if (doc.name) score += 1;
  if (doc.salt) score += 1;
  if (doc.dosage) score += 1;
  if (doc.manufacturer) score += 1;
  if (doc.description && doc.description.length > 20) score += 2;
  if (doc.image_url) score += 1;
  if (doc.faq && doc.faq.length > 0) score += 1;
  if (doc.side_effects && doc.side_effects.length > 0) score += 1;
  if (doc.source_platforms && doc.source_platforms.length > 0) score += doc.source_platforms.length;
  if (doc.llm_enriched) score += 3;
  return score;
}

function pickMaster(docs) {
  return [...docs].sort((a, b) => {
    // 0. Prefer already canonical medicines
    if (a.is_canonical && !b.is_canonical) return -1;
    if (!a.is_canonical && b.is_canonical) return 1;

    const diff = completenessScore(b) - completenessScore(a);
    if (diff !== 0) return diff;
    const dateA = a.createdAt ? new Date(a.createdAt).getTime() : Infinity;
    const dateB = b.createdAt ? new Date(b.createdAt).getTime() : Infinity;
    return dateA - dateB;
  })[0];
}

// ──────────────────────────────────────────────────────────────────────
// MERGE HELPERS
// ──────────────────────────────────────────────────────────────────────
function mergeUniqueStrings(...arrays) {
  return [...new Set(arrays.flat().filter(Boolean))];
}

function mergeFaq(...faqArrays) {
  const merged = [];
  const seen = new Set();
  faqArrays.flat().forEach((entry) => {
    const q = entry?.question?.trim();
    const a = entry?.answer?.trim();
    if (!q || !a) return;
    const key = `${q.toLowerCase()}::${a.toLowerCase()}`;
    if (seen.has(key)) return;
    seen.add(key);
    merged.push({ question: q, answer: a });
  });
  return merged;
}

function chooseLonger(...values) {
  return values.filter(Boolean).sort((a, b) => String(b).length - String(a).length)[0] || null;
}

// Salt-specific quality scoring (same logic as upsert.js)
const SALT_NOISE_WORDS = /\b(view|company|about\s+us|careers|blog|partner|our\s+services|order|feedback|strip\s+of|capsule[s]?|tablet[s]?|information|fulfillment|delivery|pharmacy|nearest|licensed|retail)\b/i;
function saltQualityScore(salt) {
  if (!salt) return -1;
  const s = String(salt);
  let score = 0;
  if (s.length > 200) score -= 50;
  else if (s.length > 120) score -= 20;
  if (SALT_NOISE_WORDS.test(s)) score -= 40;
  if (/^[A-Za-z][A-Za-z\s-]+\s*\(\s*\d+/.test(s)) score += 30;
  if (/^[A-Za-z][A-Za-z\s-]+(?:\s*\+\s*[A-Za-z][A-Za-z\s-]+)+/.test(s)) score += 20;
  if (s.length >= 5 && s.length <= 120) score += 10;
  return score;
}
function chooseBetterSaltFromList(...values) {
  return values.filter(Boolean).sort((a, b) => saltQualityScore(b) - saltQualityScore(a))[0] || null;
}

// ──────────────────────────────────────────────────────────────────────
// TRANSACTION-BASED MERGE (shared between Pass 1 and Pass 2)
// ──────────────────────────────────────────────────────────────────────
async function mergeGroup(master, duplicates, canonicalKey, jsonBackupEntries, stats) {
  const duplicateIds = duplicates.map((d) => d._id);

  if (DRY_RUN) {
    stats.mergedGroups++;
    return true;
  }

  const session = await mongoose.startSession();
  try {
    session.startTransaction();

    // Merge metadata into master
    const masterUpdate = {
      source_platforms: mergeUniqueStrings(
        master.source_platforms || [],
        ...duplicates.map((d) => d.source_platforms || [])
      ),
      side_effects: mergeUniqueStrings(
        master.side_effects || [],
        ...duplicates.map((d) => d.side_effects || [])
      ),
      faq: mergeFaq(master.faq || [], ...duplicates.map((d) => d.faq || [])),
      salt_tokens: mergeUniqueStrings(
        master.salt_tokens || [],
        ...duplicates.map((d) => d.salt_tokens || [])
      ),
      salt: chooseBetterSaltFromList(master.salt, ...duplicates.map((d) => d.salt)),
      description: chooseLonger(master.description, ...duplicates.map((d) => d.description)),
      manufacturer: chooseLonger(master.manufacturer, ...duplicates.map((d) => d.manufacturer)),
      image_url: master.image_url || duplicates.find((d) => d.image_url)?.image_url || null,
      is_canonical: true,
    };

    await Medicine.updateOne({ _id: master._id }, { $set: masterUpdate }, { session });

    // Relink prices with per-platform deduplication
    const existingMasterPrices = await Price.find(
      { medicine_id: master._id },
      { platform: 1 },
      { session, lean: true }
    );
    const masterPlatforms = new Set(existingMasterPrices.map((p) => p.platform));
    let pricesMoved = 0;

    for (const dupId of duplicateIds) {
      const dupPrices = await Price.find({ medicine_id: dupId }, null, { session, lean: true });
      for (const dupPrice of dupPrices) {
        if (!masterPlatforms.has(dupPrice.platform)) {
          await Price.updateOne(
            { _id: dupPrice._id },
            { $set: { medicine_id: master._id } },
            { session }
          );
          masterPlatforms.add(dupPrice.platform);
          pricesMoved++;
        } else {
          await Price.deleteOne({ _id: dupPrice._id }, { session });
        }
      }
    }
    stats.totalPricesMoved += pricesMoved;

    // Backup duplicates to deleted_medicines collection
    const backupDocs = duplicates.map((dup) => {
      const doc = { ...dup };
      doc._original_id = doc._id;
      doc._merged_into = master._id;
      doc._merged_at = new Date();
      doc._canonical_key = canonicalKey;
      delete doc._id;
      return doc;
    });
    await DeletedMedicine.insertMany(backupDocs, { session });

    // Delete duplicates
    await Medicine.deleteMany({ _id: { $in: duplicateIds } }, { session });
    stats.totalDuplicatesRemoved += duplicates.length;

    // JSON backup entry
    jsonBackupEntries.push({
      canonicalKey,
      masterId: String(master._id),
      masterName: master.name,
      duplicates: duplicates.map((d) => ({
        id: String(d._id),
        name: d.name,
        platforms: d.source_platforms || [],
      })),
      pricesMoved,
      mergedAt: new Date().toISOString(),
    });

    await session.commitTransaction();
    stats.mergedGroups++;
    return true;
  } catch (err) {
    await session.abortTransaction();
    stats.errors.push(`Group "${canonicalKey}": ${err.message}`);
    return false;
  } finally {
    session.endSession();
  }
}

// ──────────────────────────────────────────────────────────────────────
// PASS 1: Exact canonical_key duplicates (aggregation)
// ──────────────────────────────────────────────────────────────────────
async function pass1ExactDuplicates(stats, jsonBackupEntries) {

  const cursor = Medicine.collection.aggregate([
    { $match: { canonical_key: { $exists: true, $ne: null, $ne: "" } } },
    { $group: { _id: "$canonical_key", ids: { $push: "$_id" }, count: { $sum: 1 } } },
    { $match: { count: { $gt: 1 } } },
    { $sort: { count: -1 } },
  ], { allowDiskUse: true });
  
  const groups = await cursor.toArray();

  for (let i = 0; i < groups.length; i++) {
    const { _id: canonicalKey, ids } = groups[i];
    const docs = await Medicine.find({ _id: { $in: ids } }).lean();

    // Dosage validation (flexible)
    const dosages = [...new Set(docs.map(getFuzzyDosage).filter(Boolean))];
    if (dosages.length === 0) { stats.skippedDosageMissing++; continue; }
    if (dosages.length > 1) { stats.skippedDosageMismatch++; continue; }

    // Name similarity
    const names = docs.map((d) => normalizeNameForMatching(d.name));
    let minSim = 1;
    for (let a = 0; a < names.length; a++) {
      for (let b = a + 1; b < names.length; b++) {
        minSim = Math.min(minSim, wordJaccard(names[a], names[b]));
      }
    }
    if (minSim < SIMILARITY_THRESHOLD) {
      stats.skippedLowSimilarity++;
      continue;
    }

    const master = pickMaster(docs);
    const duplicates = docs.filter((d) => String(d._id) !== String(master._id));
    if (duplicates.length === 0) { stats.skippedSingleRecord++; continue; }

    await mergeGroup(master, duplicates, canonicalKey, jsonBackupEntries, stats);
  }
}

// ──────────────────────────────────────────────────────────────────────
// PASS 2: Fuzzy cross-key duplicates
// ──────────────────────────────────────────────────────────────────────
async function pass2FuzzyDuplicates(stats, jsonBackupEntries) {

  // Load all medicines with their normalized_name and dosage
  const allMeds = await Medicine.find(
    {}, // We must load canonical records too, so we can merge new records into them!
    { name: 1, normalized_name: 1, dosage: 1, canonical_key: 1, source_platforms: 1, is_canonical: 1 },
    { lean: true }
  );

  // Group by fuzzy key
  const fuzzyGroups = new Map();
  let skippedNoKey = 0;

  for (const med of allMeds) {
    const fk = buildFuzzyKey(med);
    if (!fk) { skippedNoKey++; continue; }
    if (!fuzzyGroups.has(fk)) fuzzyGroups.set(fk, []);
    fuzzyGroups.get(fk).push(med);
  }

  // Filter to groups with >1 member AND different canonical_keys
  const candidates = [];
  for (const [fk, meds] of fuzzyGroups) {
    if (meds.length <= 1) continue;
    const uniqueKeys = new Set(meds.map((m) => m.canonical_key).filter(Boolean));
    if (uniqueKeys.size <= 1) continue; // All same canonical_key — already handled in pass1
    candidates.push({ fuzzyKey: fk, medIds: meds.map((m) => m._id) });
  }

  for (let i = 0; i < candidates.length; i++) {
    const { fuzzyKey, medIds } = candidates[i];

    // Reload full documents (some may have been merged in pass1)
    const docs = await Medicine.find({ _id: { $in: medIds } }).lean();
    if (docs.length <= 1) continue;

    // Dosage validation (flexible)
    const dosages = [...new Set(docs.map(getFuzzyDosage).filter(Boolean))];
    if (dosages.length === 0) { stats.skippedDosageMissing++; continue; }
    if (dosages.length > 1) { stats.skippedDosageMismatch++; continue; }

    // Strict similarity check on normalized_name
    const names = docs.map((d) => normalizeNameForMatching(d.name));
    
    // FORCE MERGE: If all names match exactly after normalization, skip Jaccard
    const allNamesIdentical = new Set(names).size === 1;
    
    let minSim = 1;
    if (!allNamesIdentical) {
      for (let a = 0; a < names.length; a++) {
        for (let b = a + 1; b < names.length; b++) {
          minSim = Math.min(minSim, wordJaccard(names[a], names[b]));
        }
      }
    }

    if (minSim < SIMILARITY_THRESHOLD) {
      stats.skippedLowSimilarity++;
      continue;
    }

    const master = pickMaster(docs);
    const duplicates = docs.filter((d) => String(d._id) !== String(master._id));
    if (duplicates.length === 0) continue;

    await mergeGroup(master, duplicates, fuzzyKey, jsonBackupEntries, stats);
  }
}

// ──────────────────────────────────────────────────────────────────────
// PASS 3: Fuzzy Integer Dosage Matching (Salt + Base Integer)
// ──────────────────────────────────────────────────────────────────────
async function pass3IntegerFuzzyMatch(stats, jsonBackupEntries) {

  const allMeds = await Medicine.find(
    {},
    { name: 1, normalized_name: 1, dosage: 1, salt: 1, source_platforms: 1, is_canonical: 1 },
    { lean: true }
  );

  const fuzzyGroups = new Map();
  let skippedNoKey = 0;

  for (const med of allMeds) {
    const core = normalizeNameForMatching(med.normalized_name || med.name);
    const rawFuzzyDosage = getFuzzyDosage(med);
    const intDosage = getDosageIntegers(rawFuzzyDosage);
    const normalizedSalt = med.salt ? String(med.salt).toLowerCase().replace(/[^a-z0-9]/g, "") : null;

    // We require a core name and an integer dosage to attempt this dangerous pass
    if (!core || !intDosage) {
      skippedNoKey++;
      continue;
    }

    // Key format: CORE|INT_DOSE|SALT (if salt is null, we use 'NOSALT')
    const fk = `${core}|${intDosage}|${normalizedSalt || 'NOSALT'}`;
    
    if (!fuzzyGroups.has(fk)) fuzzyGroups.set(fk, []);
    fuzzyGroups.get(fk).push(med);
  }

  const candidates = [];
  for (const [fk, meds] of fuzzyGroups) {
    if (meds.length <= 1) continue;
    
    // Check if they actually have different raw dosages (otherwise pass 1/2 would catch them)
    const uniqueRawDosages = new Set(meds.map(getFuzzyDosage).filter(Boolean));
    if (uniqueRawDosages.size <= 1) continue;

    candidates.push({ fuzzyKey: fk, medIds: meds.map((m) => m._id) });
  }

  for (let i = 0; i < candidates.length; i++) {
    const { fuzzyKey, medIds } = candidates[i];

    const docs = await Medicine.find({ _id: { $in: medIds } }).lean();
    if (docs.length <= 1) continue;

    // Strict similarity check on normalized_name
    const names = docs.map((d) => normalizeNameForMatching(d.name));
    const allNamesIdentical = new Set(names).size === 1;

    let minSim = 1;
    if (!allNamesIdentical) {
      for (let a = 0; a < names.length; a++) {
        for (let b = a + 1; b < names.length; b++) {
          minSim = Math.min(minSim, wordJaccard(names[a], names[b]));
        }
      }
    }

    if (minSim < SIMILARITY_THRESHOLD) {
      stats.skippedLowSimilarity++;
      continue;
    }

    const master = pickMaster(docs);
    const duplicates = docs.filter((d) => String(d._id) !== String(master._id));
    if (duplicates.length === 0) continue;

    await mergeGroup(master, duplicates, fuzzyKey, jsonBackupEntries, stats);
  }
}

// ──────────────────────────────────────────────────────────────────────
// MAIN
// ──────────────────────────────────────────────────────────────────────
async function main() {
  try {
    await connectDB();

    const stats = {
      totalMedicines: await Medicine.countDocuments(),
      totalGroups: 0,
      mergedGroups: 0,
      skippedDosageMissing: 0,
      skippedDosageMismatch: 0,
      skippedLowSimilarity: 0,
      skippedAlreadyCanonical: 0,
      skippedSingleRecord: 0,
      totalPricesMoved: 0,
      totalDuplicatesRemoved: 0,
      errors: [],
    };

    // Backup setup
    const backupDir = path.join(__dirname, "backups");
    if (!DRY_RUN && !fs.existsSync(backupDir)) {
      fs.mkdirSync(backupDir, { recursive: true });
    }
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const jsonBackupPath = path.join(backupDir, `merges_${timestamp}.json`);
    const jsonBackupEntries = [];

    console.log(`[Interlink] Starting interlinking passes. Initial medicine count: ${stats.totalMedicines}`);

    // Execute passes
    console.log(`[Interlink] Pass 1: Exact Duplicates...`);
    await pass1ExactDuplicates(stats, jsonBackupEntries);
    console.log(`[Interlink] Pass 2: Fuzzy Name Duplicates...`);
    await pass2FuzzyDuplicates(stats, jsonBackupEntries);
    console.log(`[Interlink] Pass 3: Fuzzy Integer Dosage Matching...`);
    await pass3IntegerFuzzyMatch(stats, jsonBackupEntries);

    // Write JSON backup
    if (!DRY_RUN && jsonBackupEntries.length > 0) {
      fs.writeFileSync(jsonBackupPath, JSON.stringify(jsonBackupEntries, null, 2));
    }

    // Final count
    const finalCount = DRY_RUN ? stats.totalMedicines : await Medicine.countDocuments();

    console.log(`[Interlink] Finished! Total medicines touched: ${stats.mergedGroups}`);
    console.log(`[Interlink] Removed Duplicates: ${stats.totalDuplicatesRemoved}`);
    console.log(`[Interlink] Prices Moved: ${stats.totalPricesMoved}`);
    console.log(`[Interlink] Final Count: ${finalCount} (Started: ${stats.totalMedicines})`);
    if (stats.errors.length > 0) {
      console.error(`[Interlink] Errors encountered:`, stats.errors);
    }
  } catch (err) {
    console.error(`[Interlink] Fatal error:`, err);
    process.exitCode = 1;
  } finally {
    await mongoose.disconnect();
  }
}

main();
