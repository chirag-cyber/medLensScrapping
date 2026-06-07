#!/usr/bin/env node
/**
 * CLI Tool for Medicine LLM Enrichment
 *
 * Runs the enrichment engine over incomplete medicines via Groq LLM API.
 * 
 * Usage:
 *   node enrich-medicines.js
 *   node enrich-medicines.js --limit 100
 *   node enrich-medicines.js --dry-run
 *   node enrich-medicines.js --force
 */

require("dotenv").config();
const connectDB = require("./etl/load/db");
const mongoose = require("mongoose");
const { findIncompleteMedicines, enrichBatch } = require("./etl/enrich/enricher");

const args = process.argv.slice(2);
const isDryRun = args.includes("--dry-run");
const isForce = args.includes("--force");
const limitArgIndex = args.indexOf("--limit");
const limit = limitArgIndex > -1 ? parseInt(args[limitArgIndex + 1], 10) : 50;

const delayArgIndex = args.indexOf("--delay");
const delayMs = delayArgIndex > -1 ? parseInt(args[delayArgIndex + 1], 10) : 2500;

async function run() {
  // Collect all Groq API keys (supports up to 4 for rate-limit rotation)
  const apiKeys = [
    process.env.GROQ_API_KEY,
    process.env.GROQ_API_KEY_2,
    process.env.GROQ_API_KEY_3,
    process.env.GROQ_API_KEY_4,
    process.env.GROQ_API_KEY_5,
    process.env.GROQ_API_KEY_6,
    process.env.GROQ_API_KEY_7,
    process.env.GROQ_API_KEY_8,
    process.env.GROQ_API_KEY_9,
    process.env.GROQ_API_KEY_10,
    process.env.GROQ_API_KEY_11,
    process.env.GROQ_API_KEY_12,
  ].filter(Boolean);

  if (apiKeys.length === 0) {
    console.error("[LLM Enrich] No GROQ_API_KEY found in .env. Exiting.");
    process.exit(1);
  }

  console.log(`[LLM Enrich] Loaded ${apiKeys.length} Groq API key(s) for rotation.`);

  try {
    await connectDB();
    console.log(`[LLM Enrich] Connected to DB. Finding incomplete medicines... (limit: ${limit})`);

    const medicines = await findIncompleteMedicines({ limit, forceReenrich: isForce });

    if (medicines.length === 0) {
      console.log("[LLM Enrich] No incomplete medicines found. Exiting.");
      process.exit(0);
    }

    console.log(`[LLM Enrich] Found ${medicines.length} medicines to enrich.`);

    const onProgress = (current, total, medicine) => {
      console.log(`[LLM Enrich] [${current}/${total}] Enriching: ${medicine.name || medicine.raw_name || 'Unknown'}`);
    };

    console.log(`[LLM Enrich] Starting enrichment batch...`);
    const results = await enrichBatch(medicines, {
      apiKey: apiKeys,
      dryRun: isDryRun,
      delayMs,
      onProgress
    });

    console.log(`[LLM Enrich] Finished enrichment batch. Results:`, JSON.stringify(results, null, 2));

  } catch (err) {
    console.error(`[LLM Enrich] Fatal error:`, err);
  } finally {
    mongoose.disconnect();
  }
}

run();
