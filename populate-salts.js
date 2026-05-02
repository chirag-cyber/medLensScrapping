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

  for (let i = 0; i < SALTS.length; i += BATCH_SIZE) {
    const currentBatch = SALTS.slice(i, i + BATCH_SIZE);
    const saltsString = currentBatch.join(",");
    const batchNum = Math.floor(i / BATCH_SIZE) + 1;
    const totalBatches = Math.ceil(SALTS.length / BATCH_SIZE);

    try {
      // Reduced concurrency to 1 and kept timeout at 30s
      // This is slower but guarantees better data collection
      execSync(`node "${BATCH_JOB_PATH}" "${saltsString}" --concurrency 1 --timeout 30000`, {
        stdio: "inherit",
        cwd: __dirname
      });
      
    } catch {
    }

    if (i + BATCH_SIZE < SALTS.length) {
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
  }

}

runInBatches().catch(err => {
  process.exit(1);
});
