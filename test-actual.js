require("dotenv").config();
const mongoose = require("mongoose");
const { ingestMedicineQuery } = require("./etl/batchJob");

async function main() {
  const query = process.argv[2];
  if (!query) {
    console.error(
      "Please provide a medicine or salt name as an argument. Example: node test-actual.js 'vitamin c'"
    );
    process.exit(1);
  }

  try {
    console.log(`---- RUNNING MULTI-PLATFORM INGEST FOR ${query.toUpperCase()} ----`);
    const summary = await ingestMedicineQuery(query, {
      concurrency: 4,
      timeout: 15000,
      mode: "auto",
      includeWebSearch: true,
    });

    console.log(JSON.stringify(summary, null, 2));
  } catch (error) {
    console.error(`Ingest failed: ${error.message}`);
    process.exitCode = 1;
  } finally {
    setTimeout(() => {
      mongoose.disconnect();
      process.exit(process.exitCode || 0);
    }, 1000);
  }
}

main();
