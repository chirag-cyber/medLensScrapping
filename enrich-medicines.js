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
  

  
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) {
    console.error("[LLM Enrich] GROQ_API_KEY is missing. Exiting.");
    process.exit(1);
  }

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
      apiKey,
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
