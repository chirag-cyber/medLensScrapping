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
    process.exit(1);
  }

  try {
    await connectDB();
    
    const medicines = await findIncompleteMedicines({ limit, forceReenrich: isForce });
    
    if (medicines.length === 0) {
      process.exit(0);
    }

    const onProgress = (current, total, medicine) => {
    };

    const results = await enrichBatch(medicines, {
      apiKey,
      dryRun: isDryRun,
      delayMs,
      onProgress
    });

    
    if (Object.keys(results.fieldsEnrichedCounts).length > 0) {
        Object.entries(results.fieldsEnrichedCounts).forEach(([field, count]) => {
        });
    }

    if (results.errors.length > 0) {

    }

  } catch (error) {
  } finally {
    mongoose.disconnect();
  }
}

run();
