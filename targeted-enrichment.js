#!/usr/bin/env node

/**
 * 💊 targeted-enrichment.js
 *
 * Phase 2 of the ETL pipeline.
 * Scans the database for medicines missing prices from priority platforms
 * and launches targeted web scrapers to find them.
 *
 * Multi-query cascade strategy:
 *   1. Exact name (e.g., "dolo-650 tablet")
 *   2. Brand only (e.g., "dolo")
 *   3. Brand + compact dosage (e.g., "dolo 650mg")
 *   4. Brand + spaced dosage (e.g., "dolo 650 mg tablet")
 *   5. Salt/composition fallback (e.g., "paracetamol 650mg")
 *
 * Usage:
 *   node targeted-enrichment.js --limit 10           # Run enrichment on 10 medicines
 *   node targeted-enrichment.js --limit 10 --dry-run # Preview what would be searched
 *   node targeted-enrichment.js --name "dolo"        # Test a specific medicine
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");
const { ingestMedicineQuery } = require("./etl/batchJob");

// ──────────────────────────────────────────────────────────────────────
// CONFIG
// ──────────────────────────────────────────────────────────────────────
const PRIORITY_PLATFORMS = ["1mg", "pharmeasy", "netmeds", "apollo"];
const SECONDARY_PLATFORMS = ["truemeds", "medplus"];
const ALL_PLATFORMS = [...PRIORITY_PLATFORMS, ...SECONDARY_PLATFORMS];

// Form words to strip when building brand-only queries
const FORM_WORDS = [
  "tablet", "tablets", "tab", "capsule", "capsules", "cap",
  "strip", "syrup", "suspension", "injection", "drop", "drops",
  "ointment", "cream", "gel", "spray", "sachet", "solution",
  "inhaler", "respules", "rotacaps", "eye", "ear", "nasal",
  "sr", "cr", "mr", "xr", "er", "dr", "pr", "od", "forte",
  "of", "new", "plus", "max", "ds", "ls", "junior",
];

// Parse command line args
const DRY_RUN = process.argv.includes("--dry-run");
const limitArgIndex = process.argv.indexOf("--limit");
const LIMIT = limitArgIndex > -1 ? parseInt(process.argv[limitArgIndex + 1], 10) || 50 : 50;

// Specific target for testing
const nameArgIndex = process.argv.indexOf("--name");
const TARGET_NAME = nameArgIndex > -1 ? process.argv[nameArgIndex + 1] : null;
const dosageArgIndex = process.argv.indexOf("--dosage");
const TARGET_DOSAGE = dosageArgIndex > -1 ? process.argv[dosageArgIndex + 1] : null;

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
// MODELS
// ──────────────────────────────────────────────────────────────────────
const MedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", MedicineSchema);
const PriceSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const Price = mongoose.models.Price || mongoose.model("Price", PriceSchema);

// ──────────────────────────────────────────────────────────────────────
// HELPERS
// ──────────────────────────────────────────────────────────────────────

/** Determine exactly which priority platforms a medicine is missing */
function getMissingPlatforms(sourcePlatforms = []) {
  const platforms = sourcePlatforms.map(p => p.toLowerCase());
  return PRIORITY_PLATFORMS.filter(p => !platforms.includes(p));
}

/** Sanitize a query string for safe search */
function sanitizeQuery(str) {
  return String(str || "")
    .replace(/[^a-zA-Z0-9\s.-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Extract the brand name from a medicine name by stripping dosage, form, and noise words */
function extractBrandName(name) {
  let brand = String(name || "").toLowerCase().trim();
  // Strip dosage patterns like "650mg", "500 mg", "100mg+325mg"
  brand = brand.replace(/\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)\s*(?:\+\s*\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)\s*)*/gi, "");
  // Strip standalone numbers like "650", "500"
  brand = brand.replace(/\b\d+\b/g, "");
  // Strip form words
  const words = brand.split(/\s+/).filter(w => w && !FORM_WORDS.includes(w));
  return words.join(" ").trim();
}

/** Extract first salt name from composition like "Paracetamol (650mg) + Caffeine (50mg)" */
function extractPrimarySaltName(salt) {
  if (!salt) return null;
  // Match the first salt name before the first parenthesis or +
  const match = salt.match(/^([A-Za-z][A-Za-z\s-]+?)(?:\s*\(|\s*\+|\s*$)/);
  if (match) return match[1].trim();
  return salt.split(/[+(]/)[0].trim() || null;
}

/**
 * Build an ordered list of search query variations for a medicine.
 * Tries from most specific to least specific.
 *
 * Example for "Dolo-650 Tablet" with dosage "650mg" and salt "Paracetamol (650mg)":
 *   1. "dolo-650 tablet"              → exact name
 *   2. "dolo 650mg"                   → brand + compact dosage  
 *   3. "dolo 650 mg tablet"           → brand + spaced dosage + form
 *   4. "dolo"                         → brand only
 *   5. "paracetamol 650mg"            → salt + dosage (fallback)
 */
function buildQueryVariations(med) {
  const name = String(med.name || "").trim();
  const dosage = String(med.dosage || "").trim();
  const salt = med.salt || "";
  const normalizedSalt = med.normalized_salt || "";
  const primarySaltKey = med.primary_salt_key || "";
  
  const variations = [];
  const seen = new Set();

  const addVariation = (query, label) => {
    const clean = sanitizeQuery(query);
    if (!clean || clean.length < 2) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    variations.push({ query: clean, label });
  };

  // 1. Exact name (current behavior)
  let exactQuery = name;
  const nameNoSpace = name.toLowerCase().replace(/\s+/g, "");
  const dosageNoSpace = dosage.toLowerCase().replace(/\s+/g, "");
  if (dosageNoSpace && dosageNoSpace !== "1mg" && !nameNoSpace.includes(dosageNoSpace)) {
    exactQuery += ` ${dosage}`;
  }
  addVariation(exactQuery, "exact-name");

  // Extract brand for building variations
  const brand = extractBrandName(name);

  if (brand) {
    // 2. Brand + compact dosage (e.g., "dolo 650mg")
    if (dosage && dosage !== "1mg") {
      addVariation(`${brand} ${dosage}`, "brand+dosage");
    }

    // 3. Brand + spaced dosage + form
    // Extract form word from original name
    const formMatch = name.toLowerCase().match(/\b(tablet|capsule|syrup|injection|cream|gel|spray|drop|ointment|suspension|inhaler)\b/i);
    if (dosage && dosage !== "1mg" && formMatch) {
      const spacedDosage = dosage.replace(/(\d+)\s*(mg|ml|mcg|g|gm|kg)/gi, "$1 $2");
      addVariation(`${brand} ${spacedDosage} ${formMatch[1]}`, "brand+spaced-dosage+form");
    }

    // 4. Brand only (most forgiving — works when platform uses different naming)
    if (brand.length >= 3) {
      addVariation(brand, "brand-only");
    }
  }

  // 5. Salt/composition fallback
  // Try: "paracetamol 650mg" or just "paracetamol" as last resort
  const primarySalt = extractPrimarySaltName(salt) || primarySaltKey || normalizedSalt;
  if (primarySalt && primarySalt.length >= 3) {
    if (dosage && dosage !== "1mg") {
      addVariation(`${primarySalt} ${dosage}`, "salt+dosage");
    }
    addVariation(primarySalt, "salt-only");
  }

  return variations;
}

/**
 * Check if the target medicine actually got a price for any of the missing platforms
 * after running a scrape query.
 */
async function checkIfPlatformFound(medicineId, missingPlatforms) {
  const prices = await Price.find({
    medicine_id: medicineId,
    platform: { $in: missingPlatforms }
  }, { platform: 1 }).lean();
  
  const foundPlatforms = prices.map(p => p.platform);
  const stillMissing = missingPlatforms.filter(p => !foundPlatforms.includes(p));
  return { foundPlatforms, stillMissing };
}

// ──────────────────────────────────────────────────────────────────────
// ENRICHMENT ORCHESTRATOR
// ──────────────────────────────────────────────────────────────────────
async function runEnrichment() {

  // 1. Find candidates
  const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

  let query = {};

  if (TARGET_NAME) {
    console.log(`[Targeted] Testing specific medicine: "${TARGET_NAME}"${TARGET_DOSAGE ? ` [${TARGET_DOSAGE}]` : ""}`);
    query.name = { $regex: new RegExp(TARGET_NAME, "i") };
    if (TARGET_DOSAGE) {
        query.dosage = TARGET_DOSAGE;
    }
  } else {
    query = {
      source_platforms: { $exists: true, $type: "array" },
      $or: [
        { last_enriched_at: { $exists: false } },
        { last_enriched_at: { $lt: sevenDaysAgo } }
      ]
    };
  }

  const candidates = await Medicine.find(query, {
    name: 1,
    normalized_name: 1,
    dosage: 1,
    salt: 1,
    normalized_salt: 1,
    primary_salt_key: 1,
    source_platforms: 1,
    last_enriched_at: 1
  }).lean();

  // Filter and sort candidates
  const enrichable = candidates
    .map(c => ({
      ...c,
      missingPlatforms: getMissingPlatforms(c.source_platforms)
    }))
    .filter(c => c.missingPlatforms.length > 0)
    // Sort: medicines already on 2-3 platforms are high priority to complete
    .sort((a, b) => (b.source_platforms?.length || 0) - (a.source_platforms?.length || 0))
    .slice(0, LIMIT);

  console.log(`[Targeted] Found ${enrichable.length} medicines needing platform enrichment.`);

  if (enrichable.length === 0) {
    return;
  }

  // 2. Process the batch with multi-query cascade
  let successCount = 0;
  let failCount = 0;
  let totalQueriesRun = 0;

  for (let i = 0; i < enrichable.length; i++) {
    const med = enrichable[i];
    const variations = buildQueryVariations(med);
    let missing = [...med.missingPlatforms];

    console.log(`[Targeted] [${i + 1}/${enrichable.length}] Enriching: "${med.name}" (missing: ${missing.join(', ')})`);

    if (DRY_RUN) {
      console.log(`[Targeted]   Query variations:`);
      variations.forEach((v, idx) => console.log(`    ${idx + 1}. [${v.label}] "${v.query}"`));
      continue;
    }

    // Always update last_enriched_at BEFORE running the scraper.
    await Medicine.updateOne(
      { _id: med._id },
      { $set: { last_enriched_at: new Date() } }
    );

    let anySuccess = false;

    // Try each query variation until all platforms are found or we exhaust variations
    for (const variation of variations) {
      if (missing.length === 0) break;

      try {
        console.log(`[Targeted]   → [${variation.label}] Searching: "${variation.query}" on ${missing.join(', ')}`);
        
        const result = await ingestMedicineQuery(variation.query, {
          platformIds: missing,
          perPlatformLimit: 5,      // 5 results is enough — we just need 1 match
          concurrency: 2,
          includeWebSearch: true,    // Web search is the primary fallback in fast mode
          mode: "fast",              // FAST MODE: no Puppeteer browsers — uses API/HTML + web search only
          timeout: 15000,            // 15s per page HTTP fetch
        });

        
        totalQueriesRun++;

        const discovered = result.discoveredSources || 0;
        const scraped = result.scrapedSources || 0;
        const failed = result.failedSources || 0;
        const touched = result.etl?.medicinesTouched || 0;
        const pricesUpserted = result.etl?.priceEntriesUpserted || 0;
        
        console.log(`[Targeted]     Discovered: ${discovered} | Scraped: ${scraped} | Failed: ${failed} | Medicines: ${touched} | Prices: ${pricesUpserted}`);

        // Check if target medicine actually got prices for missing platforms
        const check = await checkIfPlatformFound(med._id, missing);
        
        if (check.foundPlatforms.length > 0) {
          console.log(`[Targeted]     ✅ Found on: ${check.foundPlatforms.join(', ')}`);
          // Update source_platforms on the medicine record
          await Medicine.updateOne(
            { _id: med._id },
            { $addToSet: { source_platforms: { $each: check.foundPlatforms } } }
          );
          missing = check.stillMissing;
          anySuccess = true;
        }

        if (missing.length === 0) {
          console.log(`[Targeted]     🎉 All platforms found!`);
          break;
        }

      } catch (err) {
        console.error(`[Targeted]     ⚠️ Query failed: ${err.message}`);
      }
    }

    if (missing.length > 0) {
      console.log(`[Targeted]   ⏭️ Still missing: ${missing.join(', ')} (exhausted all ${variations.length} variations)`);
    }

    if (anySuccess) {
      successCount++;
    } else {
      failCount++;
    }
  }

  // 3. Summary
  console.log(`\n${"═".repeat(60)}`);
  console.log(`[Targeted] FINISHED!`);
  console.log(`  Medicines processed: ${enrichable.length}`);
  console.log(`  Successfully enriched: ${successCount}`);
  console.log(`  No new platforms found: ${failCount}`);
  console.log(`  Total queries executed: ${totalQueriesRun}`);
  console.log(`${"═".repeat(60)}`);
}

// ──────────────────────────────────────────────────────────────────────
// MAIN
// ──────────────────────────────────────────────────────────────────────
async function main() {
  try {
    await connectDB();
    await runEnrichment();
  } catch (err) {
    console.error(`[Targeted] Fatal error: ${err.message}`);
    process.exitCode = 1;
  } finally {
    await mongoose.disconnect();
  }
}

main();
