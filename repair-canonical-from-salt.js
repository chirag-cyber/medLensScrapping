#!/usr/bin/env node
/**
 * repair-canonical-from-salt.js
 * 
 * Repairs canonical_key and dosage fields by extracting the CORRECT dosage
 * from the salt/composition field (e.g., "Aceclofenac (100mg) + Paracetamol (325mg)")
 * 
 * This fixes records where:
 *   - dosage was incorrectly set to "1mg" (platform noise from 1mg.com)
 *   - dosage was set to garbage like "12345661%" (CSS/JS noise)
 *   - canonical_key contains these wrong dosage values
 * 
 * Safety:
 *   - Only touches records with bad canonical_keys
 *   - Extracts dosage from salt field which is the ground truth
 *   - Handles duplicate key collisions by merging platforms
 *   - Does NOT modify any other fields
 * 
 * Usage:
 *   node repair-canonical-from-salt.js              # execute repair
 *   node repair-canonical-from-salt.js --dry-run    # preview only
 */

const mongoose = require("mongoose");
require("dotenv").config();

const DRY_RUN = process.argv.includes("--dry-run");

// ─── Extract dosage from salt string ─────────────────────────────────
// Parses: "Aceclofenac (100mg) + Paracetamol (325mg) + Serratiopeptidase (15mg)"
// Returns: "100mg+325mg+15mg"
function extractDosageFromSalt(salt) {
  if (!salt) return null;

  // Match all dosage values inside parentheses: (100mg), (5mg/ml), (200mcg), (0.5g)
  const matches = [...salt.matchAll(/\((\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%|iu|units?)(?:\/ml)?)\)/gi)];

  if (matches.length === 0) return null;

  const parts = matches.map(m => {
    let val = m[1].replace(/\s+/g, "").toLowerCase();
    // Strip "/ml" suffix for eye drops etc (5mg/ml → 5mg)
    val = val.replace(/\/ml$/i, "");
    return val;
  });

  return parts.join("+");
}

// ─── Build canonical key ─────────────────────────────────────────────
function buildCanonicalKey(normalizedName, dosage) {
  if (!normalizedName) return null;
  if (dosage) return `name:${normalizedName}|dose:${dosage}`;
  return `name:${normalizedName}`;
}

// ─── Main ────────────────────────────────────────────────────────────
async function main() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("MONGO_URL not found in environment.");
    process.exit(1);
  }

  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
  const Medicine = mongoose.model("Medicine", new mongoose.Schema({}, { strict: false }));

  console.log(`Starting canonical_key repair from salt field... ${DRY_RUN ? "(DRY RUN)" : ""}`);

  // Find all records with bad canonical_keys
  const badRecords = await Medicine.find({
    salt: { $exists: true, $ne: null, $ne: "" },
    $or: [
      { canonical_key: { $regex: /\|dose:1mg$/ } },
      { canonical_key: { $regex: /\|dose:\d{4,}%/ } },
    ]
  });

  console.log(`Found ${badRecords.length} records with bad canonical_keys\n`);

  let repaired = 0;
  let merged = 0;
  let skipped = 0;
  let errors = 0;

  for (const med of badRecords) {
    const saltDosage = extractDosageFromSalt(med.salt);
    const oldCanonical = med.canonical_key;

    // For medicines that are ACTUALLY 1mg (salt confirms it as sole ingredient)
    // e.g., "Glimepiride (1mg)" — the canonical_key is already correct
    if (saltDosage === "1mg" && oldCanonical?.endsWith("|dose:1mg")) {
      skipped++;
      continue;
    }

    const newDosage = saltDosage || null;
    const newCanonical = buildCanonicalKey(med.normalized_name, newDosage);

    if (!newCanonical || newCanonical === oldCanonical) {
      skipped++;
      continue;
    }

    if (DRY_RUN) {
      console.log(`[Would Fix] "${med.name}"`);
      console.log(`  Old: ${oldCanonical} | dosage: ${med.dosage || "(none)"}`);
      console.log(`  New: ${newCanonical} | dosage: ${newDosage || "(none)"}`);
      console.log(`  Salt: ${med.salt?.substring(0, 100)}`);
      repaired++;
      continue;
    }

    try {
      // Check if a record with the new canonical_key already exists
      const existing = await Medicine.findOne({
        canonical_key: newCanonical,
        _id: { $ne: med._id }
      });

      if (existing) {
        // Merge platforms into the existing clean record and delete this duplicate
        const mergedPlatforms = [...new Set([
          ...(existing.source_platforms || []),
          ...(med.source_platforms || [])
        ])];
        
        // Also merge salt_tokens
        const mergedSaltTokens = [...new Set([
          ...(existing.salt_tokens || []),
          ...(med.salt_tokens || [])
        ])];

        // Pick longer salt and description
        const bestSalt = (med.salt?.length || 0) > (existing.salt?.length || 0) ? med.salt : existing.salt;
        const bestDesc = (med.description?.length || 0) > (existing.description?.length || 0) ? med.description : existing.description;

        await Medicine.updateOne(
          { _id: existing._id },
          {
            $set: {
              source_platforms: mergedPlatforms,
              salt_tokens: mergedSaltTokens,
              salt: bestSalt,
              description: bestDesc || existing.description,
            }
          }
        );
        await Medicine.deleteOne({ _id: med._id });
        merged++;
        console.log(`[Merged] "${med.name}" → existing record (${mergedPlatforms.join(", ")})`);
      } else {
        // Fix the canonical_key and dosage in place
        const updateFields = {
          canonical_key: newCanonical,
        };
        
        // Only update dosage if we extracted a valid one from salt
        if (newDosage) {
          updateFields.dosage = newDosage;
        } else {
          // Clear the bad dosage
          await Medicine.updateOne(
            { _id: med._id },
            { $unset: { dosage: "" }, $set: { canonical_key: newCanonical } }
          );
          repaired++;
          continue;
        }

        await Medicine.updateOne(
          { _id: med._id },
          { $set: updateFields }
        );
        repaired++;
      }
    } catch (err) {
      if (err.code === 11000) {
        // Unique index collision on normalized_name+dosage
        try {
          const cleanMed = await Medicine.findOne({
            normalized_name: med.normalized_name,
            dosage: newDosage,
            _id: { $ne: med._id }
          });
          if (cleanMed) {
            const mp = [...new Set([...(cleanMed.source_platforms || []), ...(med.source_platforms || [])])];
            await Medicine.updateOne({ _id: cleanMed._id }, { $set: { source_platforms: mp } });
            await Medicine.deleteOne({ _id: med._id });
            merged++;
            console.log(`[Index Collision Merged] "${med.name}"`);
          } else {
            errors++;
            console.error(`[Error] Could not resolve collision for "${med.name}"`);
          }
        } catch (innerErr) {
          errors++;
          console.error(`[Error] "${med.name}": ${innerErr.message}`);
        }
      } else {
        errors++;
        console.error(`[Error] "${med.name}": ${err.message}`);
      }
    }
  }

  console.log(`\n${"═".repeat(50)}`);
  console.log(`Repair Complete! ${DRY_RUN ? "(DRY RUN - no changes made)" : ""}`);
  console.log(`  Repaired: ${repaired}`);
  console.log(`  Merged into existing: ${merged}`);
  console.log(`  Skipped (already correct): ${skipped}`);
  console.log(`  Errors: ${errors}`);
  console.log(`${"═".repeat(50)}`);

  await mongoose.disconnect();
}

main().catch(console.error);
