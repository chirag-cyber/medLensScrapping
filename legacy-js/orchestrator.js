#!/usr/bin/env node
/**
 * Master Extraction Orchestrator — "The Grand Crawler"
 *
 * A fault-tolerant, resumable pipeline that maximizes medicine extraction
 * across all pharmacy platforms by systematically feeding query combinations
 * through the ETL pipeline and enriching incomplete records via LLM.
 *
 * Usage:
 *   node orchestrator.js                          # Run with salt list (default)
 *   node orchestrator.js --mode alphabet          # Run with aa-zz permutations
 *   node orchestrator.js --mode salts             # Run with clinical salt list
 *   node orchestrator.js --resume                 # Resume from last crash point
 *   node orchestrator.js --batch-size 5           # Queries per batch
 *   node orchestrator.js --enrich-interval 3      # LLM enrich every N batches
 *   node orchestrator.js --dry-run                # Preview queries without scraping
 *   node orchestrator.js --reset                  # Clear state and start fresh
 *   node orchestrator.js --skip "salt1,salt2"     # Skip specific stuck salts
 */

require("dotenv").config();
const fs = require("fs");
const path = require("path");
const mongoose = require("mongoose");
const connectDB = require("./etl/load/db");
const { ingestMedicineQuery } = require("./etl/batchJob");
const { findIncompleteMedicines, enrichBatch } = require("./etl/enrich/enricher");

// ─── Configuration ──────────────────────────────────────────────────
const STATE_FILE = path.join(__dirname, ".crawler-state.json");
const SALTS_FILE = path.join(__dirname, "salts.json");
const LOG_FILE = path.join(__dirname, "crawler.log");

const DEFAULT_CONFIG = {
  mode: "salts",             // "salts" | "alphabet"
  batchSize: 3,              // queries processed per batch
  concurrency: 2,            // parallel platform scrapes per query
  enrichInterval: 5,         // run LLM enrichment every N batches (was 1, increased to reduce rate-limit failures)
  enrichLimit: 30,           // max records to enrich per LLM run
  minDelayMs: 2000,          // minimum delay between batches
  maxDelayMs: 5000,          // maximum delay between batches
  maxRetries: 2,             // retries per failed query
  maxBatches: null,          // null = unlimited; set to e.g. 10 to exit after 10 batches
  perPlatformLimit: null,    // null = all results
  timeout: 30000,            // scrape timeout per page
  scraperMode: "auto",       // "auto" | "fast" | "browser"
  includeWebSearch: true,    // needed for TrueMeds/MedPlus (their SPA blocks headless browsers)
};

// ─── CLI Argument Parsing ───────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const config = { ...DEFAULT_CONFIG };

  const getArgValue = (flag) => {
    const idx = args.indexOf(flag);
    return idx > -1 && args[idx + 1] ? args[idx + 1] : null;
  };

  if (args.includes("--mode")) config.mode = getArgValue("--mode") || "salts";
  if (args.includes("--batch-size")) config.batchSize = parseInt(getArgValue("--batch-size"), 10) || 3;
  if (args.includes("--concurrency")) config.concurrency = parseInt(getArgValue("--concurrency"), 10) || 2;
  if (args.includes("--enrich-interval")) config.enrichInterval = parseInt(getArgValue("--enrich-interval"), 10) || 5;
  if (args.includes("--enrich-limit")) config.enrichLimit = parseInt(getArgValue("--enrich-limit"), 10) || 30;
  if (args.includes("--min-delay")) config.minDelayMs = parseInt(getArgValue("--min-delay"), 10) || 2000;
  if (args.includes("--max-delay")) config.maxDelayMs = parseInt(getArgValue("--max-delay"), 10) || 5000;
  if (args.includes("--timeout")) config.timeout = parseInt(getArgValue("--timeout"), 10) || 30000;
  if (args.includes("--web-search")) config.includeWebSearch = true;

  config.dryRun = args.includes("--dry-run");
  config.resume = args.includes("--resume");
  config.reset = args.includes("--reset");

  if (args.includes("--scraper-mode")) config.scraperMode = getArgValue("--scraper-mode") || "auto";
  if (args.includes("--max-batches")) config.maxBatches = parseInt(getArgValue("--max-batches"), 10) || null;

  // --skip flag: comma-separated list of salts to skip
  if (args.includes("--skip")) {
    const skipVal = getArgValue("--skip") || "";
    config.skipQueries = skipVal.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
  } else {
    config.skipQueries = [];
  }

  return config;
}

// ─── Query Generators ───────────────────────────────────────────────

/**
 * Load clinical salt list from salts.json
 */
function loadSaltQueries() {
  if (!fs.existsSync(SALTS_FILE)) {
    process.exit(1);
  }
  const salts = JSON.parse(fs.readFileSync(SALTS_FILE, "utf8"));
  return salts;
}

/**
 * Generate 2-letter alphabet permutations: aa, ab, ac ... zy, zz
 */
function generateAlphabetQueries() {
  const queries = [];
  const letters = "abcdefghijklmnopqrstuvwxyz";
  for (const first of letters) {
    for (const second of letters) {
      queries.push(first + second);
    }
  }
  return queries;
}

/**
 * Get the full query list based on mode.
 */
function getQueries(mode) {
  switch (mode) {
    case "alphabet":
      return generateAlphabetQueries();
    case "salts":
    default:
      return loadSaltQueries();
  }
}

// ─── State Management ───────────────────────────────────────────────

function loadState() {
  if (!fs.existsSync(STATE_FILE)) return null;
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return null;
  }
}

function saveState(state) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

function resetState() {
  if (fs.existsSync(STATE_FILE)) {
    fs.unlinkSync(STATE_FILE);
  }
}

function initState(queries, mode) {
  return {
    mode,
    totalQueries: queries.length,
    currentIndex: 0,
    batchesCompleted: 0,
    processedQueries: [],
    processedUrls: [],           // dedup tracking
    failedQueries: [],
    completed: false,
    stats: {
      totalDiscovered: 0,
      totalScraped: 0,
      totalFailed: 0,
      totalMedicinesTouched: 0,
      totalPricesUpserted: 0,
      totalEnriched: 0,
      llmCallsMade: 0,
    },
    startedAt: new Date().toISOString(),
    lastUpdatedAt: new Date().toISOString(),
  };
}

// ─── Logging ────────────────────────────────────────────────────────

function log(message) {
  const timestamp = new Date().toISOString();
  const line = `[${timestamp}] ${message}`;
  console.log(message); // Output to terminal
  try {
    fs.appendFileSync(LOG_FILE, line + "\n");
  } catch {
    // silently fail if log write fails
  }
}

// ─── Utilities ──────────────────────────────────────────────────────

function randomDelay(min, max) {
  const ms = Math.floor(Math.random() * (max - min + 1)) + min;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatDuration(ms) {
  const seconds = Math.floor(ms / 1000) % 60;
  const minutes = Math.floor(ms / 60000) % 60;
  const hours = Math.floor(ms / 3600000);
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

// ─── Core Orchestration ─────────────────────────────────────────────

/**
 * Process a single query with retry logic.
 */
async function processQuery(query, config, state) {
  let lastError = null;

  for (let attempt = 1; attempt <= config.maxRetries + 1; attempt++) {
    try {
      log(`  🔍 [Attempt ${attempt}] Scraping: "${query}"`);

      const result = await ingestMedicineQuery(query, {
        perPlatformLimit: config.perPlatformLimit,
        concurrency: config.concurrency,
        timeout: config.timeout,
        mode: config.scraperMode,
        includeWebSearch: config.includeWebSearch,
      });

      // Track discovered URLs for dedup
      if (result.sources) {
        const newUrls = result.sources
          .map((s) => s.url)
          .filter((url) => !state.processedUrls.includes(url));
        state.processedUrls.push(...newUrls);

        // Keep processedUrls manageable (last 10,000)
        if (state.processedUrls.length > 10000) {
          state.processedUrls = state.processedUrls.slice(-10000);
        }
      }

      // Accumulate stats
      state.stats.totalDiscovered += result.discoveredSources || 0;
      state.stats.totalScraped += result.scrapedSources || 0;
      state.stats.totalFailed += result.failedSources || 0;
      state.stats.totalMedicinesTouched += result.etl?.medicinesTouched || 0;
      state.stats.totalPricesUpserted += result.etl?.priceEntriesUpserted || 0;

      log(`  ✅ "${query}" → Discovered: ${result.discoveredSources}, Scraped: ${result.scrapedSources}, Medicines: ${result.etl?.medicinesTouched || 0}`);
      return { success: true, result };

    } catch (err) {
      lastError = err;
      log(`  ⚠️  [Attempt ${attempt}] "${query}" failed: ${err.message}`);

      if (attempt <= config.maxRetries) {
        const retryDelay = 3000 * attempt; // exponential-ish backoff
        log(`  ⏳ Retrying in ${retryDelay / 1000}s...`);
        await new Promise((r) => setTimeout(r, retryDelay));
      }
    }
  }

  // All retries exhausted
  state.failedQueries.push({ query, error: lastError?.message || "Unknown error", timestamp: new Date().toISOString() });
  log(`  ❌ "${query}" failed after ${config.maxRetries + 1} attempts.`);
  return { success: false, error: lastError?.message };
}

/**
 * Run LLM enrichment for incomplete records.
 */
async function runEnrichmentCycle(config, state) {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) {
    log("  ⏭️  Skipping LLM enrichment (GROQ_API_KEY not set)");
    return;
  }

  log("🧠 Running LLM Enrichment Cycle...");

  try {
    const medicines = await findIncompleteMedicines({ limit: config.enrichLimit });

    if (medicines.length === 0) {
      log("  ✅ No incomplete medicines to enrich.");
      return;
    }

    log(`  🎯 Found ${medicines.length} medicines needing enrichment.`);

    const results = await enrichBatch(medicines, {
      apiKey,
      dryRun: config.dryRun,
      delayMs: 2500,
      onProgress: (current, total, med) => {
        log(`  🧠 [${current}/${total}] Enriching: ${med.name}`);
      },
    });

    state.stats.totalEnriched += results.successfulUpdates;
    state.stats.llmCallsMade += results.totalProcessed;

    log(`  ✅ Enrichment done: ${results.successfulUpdates} updated, ${results.failedUpdates} failed, ${results.skipped} skipped.`);
    if (results.errors.length > 0) {
      const topErrors = results.errors.slice(0, 3);
      topErrors.forEach(e => log(`     ⚠️  "${e.name}": ${e.error}`));
      if (results.errors.length > 3) log(`     ... and ${results.errors.length - 3} more errors.`);
    }
  } catch (err) {
    log(`  ❌ Enrichment error: ${err.message}`);
  }
}

/**
 * The main orchestration loop.
 */
async function orchestrate(config) {

  if (config.reset) {
    resetState();
  }

  await connectDB();

  // Load or generate queries
  const allQueries = getQueries(config.mode);

  // Load or init state
  let state = loadState();
  const shouldResume = state && state.mode === config.mode && !state.completed && !config.reset;

  if (shouldResume) {
    log(`♻️  Automatically resuming incomplete crawl from batch index ${state.currentIndex} (${state.batchesCompleted} batches already done)`);
  } else {
    if (state && state.completed) {
      log(`♻️  Previous crawl cycle was completed. Starting a new fresh crawl.`);
    } else if (config.reset) {
      log(`♻️  Reset requested. Starting a new fresh crawl.`);
    } else {
      log(`🆕 Starting fresh crawl with ${allQueries.length} queries.`);
    }
    state = initState(allQueries, config.mode);
    saveState(state);
  }

  // Filter out already-processed queries
  const remainingQueries = allQueries.slice(state.currentIndex);
  const totalBatches = Math.ceil(remainingQueries.length / config.batchSize);

  log(`📊 ${remainingQueries.length} queries remaining across ${totalBatches} batches.\n`);

  if (config.dryRun) {
    log("🔎 DRY RUN — Showing first 20 queries that would be processed:");
    remainingQueries.slice(0, 20).forEach((q, i) => log(`  ${i + 1}. ${q}`));
    if (remainingQueries.length > 20) log(`  ... and ${remainingQueries.length - 20} more.`);
    mongoose.disconnect();
    return;
  }

  const crawlStartTime = Date.now();

  // Main batch loop
  for (let batchIdx = 0; batchIdx < totalBatches; batchIdx++) {
    // Exit early if --max-batches limit reached
    if (config.maxBatches && batchIdx >= config.maxBatches) {
      log(`\n⏸️  Reached --max-batches limit (${config.maxBatches}). Saving state and yielding to pipeline...`);
      saveState(state);
      break;
    }

    const batchStart = batchIdx * config.batchSize;
    const batchQueries = remainingQueries.slice(batchStart, batchStart + config.batchSize);
    const globalBatchNum = state.batchesCompleted + 1;

    log(`\n${"═".repeat(60)}`);
    log(`📦 BATCH ${globalBatchNum} — Processing ${batchQueries.length} queries [${batchQueries.join(", ")}]`);
    log(`${"═".repeat(60)}`);

    // Process each query in the batch sequentially
    for (const query of batchQueries) {
      // Skip if already processed
      if (state.processedQueries.includes(query)) {
        log(`  ⏭️  Skipping "${query}" (already processed)`);
        continue;
      }

      // Skip if in --skip list
      if (config.skipQueries.length > 0 && config.skipQueries.includes(query.toLowerCase())) {
        log(`  ⏭️  Skipping "${query}" (in --skip list)`);
        state.processedQueries.push(query);
        continue;
      }

      await processQuery(query, config, state);
      state.processedQueries.push(query);

      // Small delay between queries within a batch
      await randomDelay(config.minDelayMs, config.maxDelayMs);
    }

    // Update state
    state.currentIndex += batchQueries.length;
    state.batchesCompleted = globalBatchNum;
    state.lastUpdatedAt = new Date().toISOString();
    saveState(state);

    // LLM enrichment cycle
    if (globalBatchNum % config.enrichInterval === 0) {
      await runEnrichmentCycle(config, state);
      saveState(state);
    }

    // Progress report
    const elapsed = Date.now() - crawlStartTime;
    const queriesPerSec = (state.currentIndex / (elapsed / 1000)).toFixed(2);
    const eta = totalBatches > 0
      ? formatDuration(((totalBatches - batchIdx - 1) / (batchIdx + 1)) * elapsed)
      : "unknown";

    log(`\n📊 Progress: ${state.currentIndex}/${allQueries.length} queries (${((state.currentIndex / allQueries.length) * 100).toFixed(1)}%) | Speed: ${queriesPerSec} q/s | ETA: ${eta}`);
    log(`   Discovered: ${state.stats.totalDiscovered} | Scraped: ${state.stats.totalScraped} | Medicines: ${state.stats.totalMedicinesTouched} | Enriched: ${state.stats.totalEnriched}`);

    // Batch breathing delay
    if (batchIdx < totalBatches - 1) {
      const breathe = Math.floor(Math.random() * 3000) + 2000;
      log(`💤 Breathing for ${(breathe / 1000).toFixed(1)}s before next batch...\n`);
      await new Promise((r) => setTimeout(r, breathe));
    }
  }

  // Final enrichment sweep
  log("\n🧹 Running final LLM enrichment sweep...");
  await runEnrichmentCycle(config, state);

  if (state.currentIndex >= allQueries.length) {
    state.completed = true;
    log("\n🏁 Crawl cycle completed successfully! Next run will start from the beginning.");
  }
  saveState(state);

  // Final report
  const totalElapsed = Date.now() - crawlStartTime;

  if (state.failedQueries.length > 0) {
    log("\n❌ Failed Queries:");
    state.failedQueries.forEach((f) => log(`  - "${f.query}": ${f.error}`));
  }

  mongoose.disconnect();
}

// ─── Graceful Shutdown ──────────────────────────────────────────────
let shuttingDown = false;

function handleShutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;

  log(`\n⚠️  Received ${signal}. Saving state and exiting gracefully...`);

  // State is already auto-saved after each batch, so we just exit
  log("💾 State saved. You can resume with: node orchestrator.js --resume");
  process.exit(0);
}

process.on("SIGINT", () => handleShutdown("SIGINT"));
process.on("SIGTERM", () => handleShutdown("SIGTERM"));

// ─── Entry Point ────────────────────────────────────────────────────
const config = parseArgs();
orchestrate(config).catch((err) => {
  log(`💥 Fatal error: ${err.message}`);
  process.exit(1);
});
