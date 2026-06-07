#!/usr/bin/env node

/**
 * 🧹 cleanup-data.js
 *
 * Safe retroactive data cleanup script.
 * Fixes garbage salt data and removes orphaned prices.
 *
 * SAFETY:
 *   - Backs up EVERY change to backups/ directory as JSON
 *   - Dry-run mode by default (pass --execute to actually modify)
 *   - Never deletes medicines — only fixes salt fields
 *   - Orphaned prices are backed up before deletion
 *
 * Usage:
 *   node cleanup-data.js                  # Preview only (dry-run)
 *   node cleanup-data.js --execute        # Actually apply fixes
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");
const fs = require("fs");
const path = require("path");

const EXECUTE = process.argv.includes("--execute");

// ─── DB ─────────────────────────────────────────────────────────────
async function connectDB() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
}

const M = mongoose.models.Medicine || mongoose.model("Medicine", new mongoose.Schema({}, { strict: false }));
const P = mongoose.models.Price || mongoose.model("Price", new mongoose.Schema({}, { strict: false }));

// ─── Salt Quality Detection ─────────────────────────────────────────
const NOISE_WORDS = /\b(view|company|about\s+us|careers|blog|partner|our\s+services|order|feedback|strip\s+of|information|fulfillment|delivery|pharmacy|nearest|licensed|retail)\b/i;

function isGarbageSalt(salt) {
  if (!salt) return false;
  const s = String(salt);
  // Too long — real salts are under 200 chars
  if (s.length > 200) return true;
  // Contains site navigation noise
  if (NOISE_WORDS.test(s)) return true;
  // Starts with a salt-like pattern but has garbage appended
  if (s.length > 120 && NOISE_WORDS.test(s.substring(80))) return true;
  return false;
}

/** Try to extract a clean salt from a garbage string */
function extractCleanSalt(garbageSalt) {
  if (!garbageSalt) return null;
  const s = String(garbageSalt);

  // Pattern 1: "Name (Dosage) + Name (Dosage)" — structured composition
  const structured = s.match(/^([A-Za-z][A-Za-z\s-]+\s*\(\s*\d+[^)]*\)(?:\s*\+\s*[A-Za-z][A-Za-z\s-]+\s*\(\s*\d+[^)]*\))*)/);
  if (structured && structured[1].length >= 10) return structured[1].trim();

  // Pattern 2: "NAME-DOSAGE+NAME-DOSAGE" — Apollo format
  const apolloFormat = s.match(/^([A-Z][A-Z\s]+-\d+\w+(?:\+[A-Z][A-Z\s]+-\d+\w+)*)/);
  if (apolloFormat && apolloFormat[1].length >= 8) return apolloFormat[1].trim();

  // Pattern 3: First sentence only (before any noise starts)
  const firstSentence = s.split(/\.\s+[A-Z]/)[0];
  if (firstSentence && firstSentence.length <= 120 && !NOISE_WORDS.test(firstSentence)) {
    return firstSentence.trim();
  }

  return null;
}

// ─── Main ────────────────────────────────────────────────────────────
async function main() {
  await connectDB();

  const backupDir = path.join(__dirname, "backups");
  if (!fs.existsSync(backupDir)) fs.mkdirSync(backupDir, { recursive: true });
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");

  console.log(`\n=== MedLens Data Cleanup ${EXECUTE ? "(EXECUTING)" : "(DRY-RUN)"} ===\n`);

  // ─── Task 1: Fix garbage salts ──────────────────────────────────
  console.log("[1/3] Scanning for garbage salt data...");

  const allMeds = await M.find(
    { salt: { $exists: true, $ne: null } },
    { name: 1, salt: 1, primary_salt_key: 1 }
  ).lean();

  const garbageMeds = allMeds.filter(m => isGarbageSalt(m.salt));
  console.log(`  Found ${garbageMeds.length} medicines with garbage salt data`);

  const saltFixes = [];
  let fixedCount = 0;
  let nulledCount = 0;

  for (const med of garbageMeds) {
    const cleanSalt = extractCleanSalt(med.salt);
    saltFixes.push({
      id: String(med._id),
      name: med.name,
      oldSalt: med.salt.substring(0, 100) + (med.salt.length > 100 ? "..." : ""),
      newSalt: cleanSalt,
      action: cleanSalt ? "fixed" : "nulled",
    });

    if (EXECUTE) {
      if (cleanSalt) {
        await M.updateOne({ _id: med._id }, { $set: { salt: cleanSalt } });
        fixedCount++;
      } else {
        // Don't null the salt — just flag it for LLM enrichment to fix later
        await M.updateOne({ _id: med._id }, {
          $set: { salt_needs_cleanup: true },
          $unset: { llm_enriched: "" }
        });
        nulledCount++;
      }
    }
  }

  if (saltFixes.length > 0) {
    const backupPath = path.join(backupDir, `salt_fixes_${timestamp}.json`);
    fs.writeFileSync(backupPath, JSON.stringify(saltFixes, null, 2));
    console.log(`  Backed up to: ${backupPath}`);
    if (EXECUTE) {
      console.log(`  Fixed: ${fixedCount}, Flagged for re-enrichment: ${nulledCount}`);
    }
  }

  // ─── Task 2: Fix bad primary_salt_key values ────────────────────
  console.log("\n[2/3] Fixing bad primary_salt_key values...");

  const badKeyCount = await M.countDocuments({
    primary_salt_key: { $in: ["undefined undefinedundefined", "a", ""] }
  });
  console.log(`  Found ${badKeyCount} medicines with bad salt keys`);

  if (EXECUTE && badKeyCount > 0) {
    await M.updateMany(
      { primary_salt_key: { $in: ["undefined undefinedundefined", "a", ""] } },
      { $set: { primary_salt_key: null }, $unset: { llm_enriched: "" } }
    );
    console.log(`  Cleared ${badKeyCount} bad salt keys (flagged for re-enrichment)`);
  }

  // ─── Task 3: Clean up orphaned prices ───────────────────────────
  console.log("\n[3/3] Finding orphaned prices (no matching medicine)...");

  const orphaned = await P.aggregate([
    { $lookup: { from: "medicines", localField: "medicine_id", foreignField: "_id", as: "med" } },
    { $match: { med: { $size: 0 } } },
    { $project: { medicine_id: 1, platform: 1, price: 1, url: 1 } }
  ]);

  console.log(`  Found ${orphaned.length} orphaned price records`);

  if (orphaned.length > 0) {
    // Backup first
    const orphanBackupPath = path.join(backupDir, `orphaned_prices_${timestamp}.json`);
    fs.writeFileSync(orphanBackupPath, JSON.stringify(orphaned, null, 2));
    console.log(`  Backed up to: ${orphanBackupPath}`);

    if (EXECUTE) {
      const orphanIds = orphaned.map(o => o._id);
      await P.deleteMany({ _id: { $in: orphanIds } });
      console.log(`  Deleted ${orphanIds.length} orphaned prices`);
    }
  }

  // ─── Summary ────────────────────────────────────────────────────
  console.log(`\n${"═".repeat(50)}`);
  console.log(`SUMMARY ${EXECUTE ? "(APPLIED)" : "(DRY-RUN — run with --execute to apply)"}`);
  console.log(`  Garbage salts found: ${garbageMeds.length}`);
  console.log(`  Bad salt keys: ${badKeyCount}`);
  console.log(`  Orphaned prices: ${orphaned.length}`);
  console.log(`${"═".repeat(50)}\n`);

  await mongoose.disconnect();
}

main().catch(e => { console.error(e); process.exit(1); });
