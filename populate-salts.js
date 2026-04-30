#!/usr/bin/env node

const { execSync } = require("child_process");
const path = require("path");

// The list of salts provided by the user
const SALTS = [
  "Paracetamol",
  "Ibuprofen",
  "Diclofenac",
  "Aspirin",
  "Aceclofenac",
  "Amoxicillin",
  "Azithromycin",
  "Ciprofloxacin",
  "Ceftriaxone",
  "Doxycycline",
  "Cetirizine",
  "Levocetirizine",
  "Chlorpheniramine",
  "Dextromethorphan",
  "Metformin",
  "Glimepiride",
  "Insulin",
  "Vildagliptin",
  "Amlodipine",
  "Losartan",
  "Atenolol",
  "Telmisartan",
  "Pantoprazole",
  "Omeprazole",
  "Ranitidine",
  "Vitamin D3",
  "Calcium Carbonate",
  "Ferrous Sulfate",
  "Folic Acid",
  "Multivitamin"
];

const BATCH_SIZE = 1; // Process ONE salt at a time for maximum stealth
const BATCH_JOB_PATH = path.join(__dirname, "batch-job.js");

async function runInBatches() {
  console.log(`\n🚀 Starting Ultra-Stable Bulk Ingestion for ${SALTS.length} salts\n`);
  console.log(`📡 Sequential mode: 1 salt at a time | Platform delays enabled\n`);

  for (let i = 0; i < SALTS.length; i += BATCH_SIZE) {
    const currentBatch = SALTS.slice(i, i + BATCH_SIZE);
    const saltsString = currentBatch.join(",");
    const batchNum = Math.floor(i / BATCH_SIZE) + 1;
    const totalBatches = Math.ceil(SALTS.length / BATCH_SIZE);

    console.log(`\n📦 [Salt ${batchNum}/${totalBatches}] Processing: ${saltsString}`);
    console.log(`--------------------------------------------------------------------------`);

    try {
      // Reduced concurrency to 1 and kept timeout at 30s
      // This is slower but guarantees better data collection
      execSync(`node "${BATCH_JOB_PATH}" "${saltsString}" --concurrency 1 --timeout 30000`, {
        stdio: "inherit",
        cwd: __dirname
      });
      
      console.log(`✅ [Salt ${batchNum}] Successfully completed.`);
    } catch (error) {
      console.error(`❌ [Salt ${batchNum}] Failed with error: ${error.message}`);
    }

    if (i + BATCH_SIZE < SALTS.length) {
      console.log(`\n⏳ Cooling down for 5 seconds...\n`);
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
  }

  console.log(`\n🎯 Ingestion cycle complete.`);
}

runInBatches().catch(err => {
  console.error("Fatal Error in batch runner:", err);
  process.exit(1);
});
