#!/usr/bin/env node

/**
 * 🩹 backfill-dosage.js
 *
 * Repairs medicines that have dosage:null but their canonical_key
 * contains dose information (e.g., "name:pyrigesic 1000|dose:1000mg").
 *
 * Also infers dosage from bare numbers in the medicine name
 * (e.g., "pyrigesic 1000 tablet" → dosage "1000mg") for tablet/capsule forms.
 *
 * Usage:
 *   node backfill-dosage.js              # execute repair
 *   node backfill-dosage.js --dry-run    # preview only
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");

const DRY_RUN = process.argv.includes("--dry-run");

async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("Missing MONGO_URL");
    process.exit(1);
  }
  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
}

const MedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", MedicineSchema);

// Forms that typically use "mg" as their dosage unit
const MG_FORMS = /\b(tablet|tablets|tab|capsule|capsules|cap|strip|sachet|sr|cr|mr|xr|er|dr|pr|od|forte)\b/i;
// Forms that typically use "ml" as their dosage unit
const ML_FORMS = /\b(syrup|suspension|drop|drops|solution|ml)\b/i;

/**
 * Extract dosage from canonical_key like "name:pyrigesic 1000|dose:1000mg"
 */
function extractDosageFromCanonicalKey(canonicalKey) {
  if (!canonicalKey) return null;
  const match = canonicalKey.match(/dose:(\d+(?:\.\d+)?(?:mg|ml|mcg|g|gm|kg|%)(?:\+\d+(?:\.\d+)?(?:mg|ml|mcg|g|gm|kg|%))*)/i);
  return match ? match[1] : null;
}

/**
 * Infer dosage from a bare number in the medicine name.
 * "pyrigesic 1000 tablet" → "1000mg"
 * "azithral 500 tablet" → "500mg"
 * Only infers for known dosage-range numbers (not pack sizes like "10", "15", "30").
 */
function inferDosageFromName(name) {
  if (!name) return null;
  const lower = name.toLowerCase();

  // Skip names that are clearly NOT dosage-bearing patterns
  // (vaccines with strain numbers, contraceptive day counts, etc.)
  if (/\b\d+\s*(?:days?|vaccine|mdi|md)\b/i.test(lower)) return null;
  if (/\bvaccine\b/i.test(lower)) return null;

  // Match bare numbers NOT followed by a unit (those would already be extracted)
  // Must be 2-5 digits AND >= 25 to avoid pack sizes and false positives
  const bareNumberMatch = lower.match(/\b(\d{2,5})\b(?!\s*(?:mg|ml|mcg|g|gm|kg|%|'s|tablet|capsule|strip|pack|days?))/i);
  if (!bareNumberMatch) return null;

  const num = parseInt(bareNumberMatch[1], 10);

  // Sanity: dosages are typically 25-10000mg or 25-500ml
  // Below 25 is almost always a pack size, combo index, or non-dosage number
  if (num < 25 || num > 10000) return null;

  // Skip common pack sizes
  const PACK_SIZE_NUMBERS = new Set([28, 30, 60, 90, 100]);
  if (PACK_SIZE_NUMBERS.has(num)) return null;

  // Determine unit based on form word in the name
  if (ML_FORMS.test(lower)) {
    return `${num}ml`;
  }
  // Default to mg for tablets/capsules/unknown
  return `${num}mg`;
}

async function main() {
  try {
    await connectDB();

    // Pass 1: Backfill from canonical_key
    const withCanonicalDose = await Medicine.find(
      {
        dosage: null,
        canonical_key: { $regex: /dose:/ },
      },
      { name: 1, canonical_key: 1, dosage: 1 }
    ).lean();

    console.log(`[Backfill] Pass 1: Found ${withCanonicalDose.length} medicines with dosage:null but dose in canonical_key`);

    let pass1Fixed = 0;
    for (const med of withCanonicalDose) {
      const dosage = extractDosageFromCanonicalKey(med.canonical_key);
      if (!dosage) continue;

      if (DRY_RUN) {
        console.log(`  [DRY] "${med.name}" → dosage: ${dosage} (from canonical_key: ${med.canonical_key})`);
      } else {
        await Medicine.updateOne({ _id: med._id }, { $set: { dosage } });
      }
      pass1Fixed++;
    }
    console.log(`[Backfill] Pass 1: ${DRY_RUN ? 'Would fix' : 'Fixed'} ${pass1Fixed} medicines\n`);

    // Pass 2: Infer from bare numbers in name
    const stillNullDosage = await Medicine.find(
      {
        dosage: null,
        canonical_key: { $not: { $regex: /dose:/ } },
      },
      { name: 1, normalized_name: 1, canonical_key: 1, dosage: 1 }
    ).lean();

    console.log(`[Backfill] Pass 2: Found ${stillNullDosage.length} medicines with dosage:null and no dose in canonical_key`);

    let pass2Fixed = 0;
    for (const med of stillNullDosage) {
      const dosage = inferDosageFromName(med.name);
      if (!dosage) continue;

      if (DRY_RUN) {
        console.log(`  [DRY] "${med.name}" → dosage: ${dosage} (inferred from name)`);
      } else {
        await Medicine.updateOne({ _id: med._id }, { $set: { dosage } });
        // Also update canonical_key if it's name-only
        if (med.canonical_key && !med.canonical_key.includes('|dose:')) {
          const newKey = `${med.canonical_key}|dose:${dosage}`;
          await Medicine.updateOne({ _id: med._id }, { $set: { canonical_key: newKey } });
        }
      }
      pass2Fixed++;
    }
    console.log(`[Backfill] Pass 2: ${DRY_RUN ? 'Would fix' : 'Fixed'} ${pass2Fixed} medicines\n`);

    console.log(`[Backfill] Total: ${pass1Fixed + pass2Fixed} medicines ${DRY_RUN ? 'would be' : ''} repaired`);

  } catch (err) {
    console.error(`[Backfill] Fatal error:`, err.message);
    process.exitCode = 1;
  } finally {
    await mongoose.disconnect();
  }
}

main();
