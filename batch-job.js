#!/usr/bin/env node

require("dotenv").config();
const mongoose = require("mongoose");
const { runBatchIngestion } = require("./etl/batchJob");

function parsePerPlatformLimit(value) {
  if (value === undefined) return null;

  const normalized = String(value).trim().toLowerCase();
  if (!normalized || normalized === "all" || normalized === "none" || normalized === "0") {
    return null;
  }

  const parsed = parseInt(normalized, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseArgs(argv) {
  const args = argv.slice(2);
  const values = {
    queries: [],
    perPlatformLimit: null,
    queryBatchSize: 2,
    concurrency: 4,
    timeout: 15000,
    mode: "auto",
    includeWebSearch: true,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];

    if (arg === "--per-platform-limit") {
      values.perPlatformLimit = parsePerPlatformLimit(args[index + 1]);
      index += 1;
    } else if (arg === "--query-batch-size") {
      values.queryBatchSize = parseInt(args[index + 1], 10) || values.queryBatchSize;
      index += 1;
    } else if (arg === "--concurrency") {
      values.concurrency = parseInt(args[index + 1], 10) || values.concurrency;
      index += 1;
    } else if (arg === "--timeout") {
      values.timeout = parseInt(args[index + 1], 10) || values.timeout;
      index += 1;
    } else if (arg === "--mode") {
      values.mode = args[index + 1] || values.mode;
      index += 1;
    } else if (arg === "--no-web-search") {
      values.includeWebSearch = false;
    } else if (!arg.startsWith("--")) {
      values.queries.push(
        ...arg
          .split(",")
          .map((query) => query.trim())
          .filter(Boolean)
      );
    }
  }

  return values;
}

async function main() {
  const args = parseArgs(process.argv);

  if (args.queries.length === 0) {
    console.error(
      'Usage: node batch-job.js "paracetamol 650,azithromycin 500" [--per-platform-limit all|25]'
    );
    process.exit(1);
  }

  try {
    const result = await runBatchIngestion(args.queries, args);
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`[BATCH JOB ERROR] ${error.message}`);
    process.exitCode = 1;
  } finally {
    await mongoose.disconnect();
  }
}

main();
