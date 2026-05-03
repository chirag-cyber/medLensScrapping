#!/usr/bin/env node

/**
 * 💊 targeted-enrichment.js
 *
 * Phase 2 of the ETL pipeline.
 * Scans the database for medicines missing prices from priority platforms
 * and launches targeted web scrapers to find them.
 *
 * Usage:
 *   node targeted-enrichment.js --limit 10           # Run enrichment on 10 medicines
 *   node targeted-enrichment.js --limit 10 --dry-run # Preview what would be searched
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");
const { runBatchIngestion } = require("./etl/batchJob");

// ──────────────────────────────────────────────────────────────────────
// CONFIG
// ──────────────────────────────────────────────────────────────────────
const PRIORITY_PLATFORMS = ["1mg", "pharmeasy", "netmeds", "apollo"];
const SECONDARY_PLATFORMS = ["truemeds", "medplus"];
const ALL_PLATFORMS = [...PRIORITY_PLATFORMS, ...SECONDARY_PLATFORMS];

// Parse command line args
const DRY_RUN = process.argv.includes("--dry-run");
const limitArgIndex = process.argv.indexOf("--limit");
const LIMIT = limitArgIndex > -1 ? parseInt(process.argv[limitArgIndex + 1], 10) || 50 : 50;

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

// ──────────────────────────────────────────────────────────────────────
// HELPERS
// ──────────────────────────────────────────────────────────────────────

/** Determine exactly which priority platforms a medicine is missing */
function getMissingPlatforms(sourcePlatforms = []) {
  const platforms = sourcePlatforms.map(p => p.toLowerCase());
  return PRIORITY_PLATFORMS.filter(p => !platforms.includes(p));
}

/** Construct a targeted search query string */
function buildTargetedQuery(name, dosage) {
  let query = String(name || "").trim();
  const d = String(dosage || "").trim();

  // If dosage isn't already in the name (ignoring spaces), append it
  const queryNoSpace = query.toLowerCase().replace(/\s+/g, "");
  const dNoSpace = d.toLowerCase().replace(/\s+/g, "");

  if (dNoSpace && !queryNoSpace.includes(dNoSpace)) {
    query += ` ${d}`;
  }

  // Strip special chars to make search safer
  return query.replace(/[^a-zA-Z0-9\s.-]/g, " ").replace(/\s+/g, " ").trim();
}

// ──────────────────────────────────────────────────────────────────────
// ENRICHMENT ORCHESTRATOR
// ──────────────────────────────────────────────────────────────────────
async function runEnrichment() {

  // 1. Find candidates
  // Criteria:
  // - Missing at least one priority platform
  // - Not recently enriched (skip if enriched in the last 7 days)
  const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

  const query = {
    source_platforms: { $exists: true, $type: 'array' },
    $or: [
      { last_enriched_at: { $exists: false } },
      { last_enriched_at: { $lt: sevenDaysAgo } }
    ]
  };

  const candidates = await Medicine.find(query, {
    name: 1,
    dosage: 1,
    source_platforms: 1,
    last_enriched_at: 1
  }).lean();

  // Filter and sort candidates
  const enrichable = candidates
    .map(c => ({
      ...c,
      missingPlatforms: getMissingPlatforms(c.source_platforms)
    }))
    // Only process those actually missing a priority platform
    .filter(c => c.missingPlatforms.length > 0)
    // Sort by popularity: medicines that already have 2-3 platforms are high priority to complete
    .sort((a, b) => (b.source_platforms?.length || 0) - (a.source_platforms?.length || 0))
    // Apply batch limit
    .slice(0, LIMIT);

  console.log(`[Targeted] Found ${enrichable.length} medicines needing platform enrichment.`);

  if (enrichable.length === 0) {
    return;
  }

  // 2. Process the batch
  let successCount = 0;
  let failCount = 0;

  for (let i = 0; i < enrichable.length; i++) {
    const med = enrichable[i];
    const targetQuery = buildTargetedQuery(med.name, med.dosage);
    const missing = med.missingPlatforms;

    console.log(`[Targeted] [${i + 1}/${enrichable.length}] Enriching: "${targetQuery}" (missing: ${missing.join(', ')})`);

    if (DRY_RUN) {
      continue;
    }

    // Always update last_enriched_at BEFORE running the scraper.
    // If the scraper hangs or the user hits Ctrl+C, we won't get stuck in an infinite loop
    // retrying the exact same medicine next time!
    await Medicine.updateOne(
      { _id: med._id },
      { $set: { last_enriched_at: new Date() } }
    );

    try {
      // Execute the targeted scrape
      // We pass perPlatformLimit: 10 to dig deeper into search results
      const result = await runBatchIngestion([targetQuery], {
        platformIds: missing,     // Only search the missing ones!
        perPlatformLimit: 10,     // Check the top 10 results to catch platform variations
        queryBatchSize: 1,
        concurrency: 1,           // Be nice to the CPU
        includeWebSearch: true   // Don't fall back to web search for exact enrichment
      });

      const summary = result.results?.[0] || {};
      const discovered = summary.discoveredSources || 0;
      const scraped = summary.scrapedSources || 0;
      const failed = summary.failedSources || 0;
      const touched = summary.etl?.medicinesTouched || 0;
      const rejected = summary.strictRejectedMedicines || 0;
      console.log(`[Targeted]   -> Discovered: ${discovered} | Scraped: ${scraped} | Failed: ${failed} | DB Upserted: ${touched} | Rejected: ${rejected}`);
      successCount++;

    } catch (err) {
      console.error(`[Targeted]   -> Failed: ${err.message}`);
      failCount++;
    }
  }

  // 3. Summary
  console.log(`[Targeted] Finished! Successfully enriched: ${successCount}, Failed: ${failCount}`);
}

// ──────────────────────────────────────────────────────────────────────
// MAIN
// ──────────────────────────────────────────────────────────────────────
async function main() {
  try {
    await connectDB();
    await runEnrichment();
  } catch (err) {
    process.exitCode = 1;
  } finally {
    await mongoose.disconnect();
  }
}

main();
