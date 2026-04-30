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
  console.log(`\n=== 🧠 Medicine LLM Enrichment Pipeline ===\n`);
  
  if (isDryRun) console.log(`⚠️  DRY RUN MODE: No database records will be updated.`);
  if (isForce) console.log(`⚠️  FORCE MODE: Will re-enrich even if llm_enriched is already true.`);
  
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) {
    console.error(`❌ Error: GROQ_API_KEY is not set in .env`);
    console.error(`Please retrieve an API key from console.groq.com and inject it.`);
    process.exit(1);
  }

  try {
    await connectDB();
    
    console.log(`\n🔍 Searching for incomplete medicines (limit: ${limit})...`);
    const medicines = await findIncompleteMedicines({ limit, forceReenrich: isForce });
    
    if (medicines.length === 0) {
      console.log(`✅ No incomplete medicines found. Pipeline complete.`);
      process.exit(0);
    }

    console.log(`🎯 Found ${medicines.length} candidates for enrichment.`);
    console.log(`⏱️  Estimated completion time: ~${Math.ceil((medicines.length * delayMs) / 1000)} seconds.\n`);

    const onProgress = (current, total, medicine) => {
      console.log(`[${current}/${total}] Enriching: ${medicine.name} (ID: ${medicine._id})`);
    };

    const results = await enrichBatch(medicines, {
      apiKey,
      dryRun: isDryRun,
      delayMs,
      onProgress
    });

    console.log(`\n=== 🎉 Enrichment Complete ===`);
    console.log(`Total Processed:    ${results.totalProcessed}`);
    console.log(`Successfully Fixed: ${results.successfulUpdates}`);
    console.log(`Already Had Fields: ${results.skipped}`);
    console.log(`Failed Updates:     ${results.failedUpdates}`);
    
    if (Object.keys(results.fieldsEnrichedCounts).length > 0) {
        console.log(`\nSpecific Fields Generated:`);
        Object.entries(results.fieldsEnrichedCounts).forEach(([field, count]) => {
           console.log(` - ${field}: ${count}`);
        });
    }

    if (results.errors.length > 0) {
        console.log(`\n❌ Errors Encountered:`);
        results.errors.forEach(err => console.log(` - [${err.name}] ${err.error}`));
    }

  } catch (error) {
    console.error(`\n❌ Fatal Pipeline Error:`, error);
  } finally {
    mongoose.disconnect();
  }
}

run();
