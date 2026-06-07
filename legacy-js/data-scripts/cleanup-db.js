#!/usr/bin/env node
/**
 * Database Cleanup Script — cleanup-db.js
 *
 * Scans every medicine record and fixes:
 *   1. Dirty salt fields (page titles, promo text, URLs stuffed into salt)
 *   2. Missing salt: tries to derive from normalized_salt / salt_tokens / name
 *   3. Dirty normalized_salt / normalized_name (page noise, HTML, URLs)
 *   4. Garbage descriptions that are just titles or scraped nav bars
 *   5. Side effects that are sentences instead of single terms
 *   6. Orphan prices (price docs with no matching medicine)
 *   7. Medicines with no name or purely numeric names
 *
 * Usage:
 *   node cleanup-db.js                  # dry run (default)
 *   node cleanup-db.js --apply          # actually write changes
 *   node cleanup-db.js --apply --verbose
 */

require("dotenv").config();
const mongoose = require("mongoose");
const connectDB = require("./etl/load/db");
const { Medicine, Price } = require("./etl/load/models");
const { cleanDescription, cleanSideEffects } = require("./etl/transform/sanitizer");

// ─── CLI flags ──────────────────────────────────────────────────────
const args = process.argv.slice(2);
const APPLY   = args.includes("--apply");
const VERBOSE = args.includes("--verbose");

// ─── Detection patterns ────────────────────────────────────────────
// These patterns indicate scraper noise shoved into a field that should
// contain a clean chemical/salt name.
const GARBAGE_SALT_PATTERNS = [
  /https?:\/\//i,                                     // URLs
  /www\.\w+/i,                                        // bare domains
  /\b(?:order|buy|add\s+to\s+cart|shop|free\s+delivery|cash\s+on|shipping|view\s+all|click\s+here|know\s+more|read\s+more)\b/i,
  /\b(?:our\s+services|about\s+us|careers|blog|partner|feedback|terms\s+and|privacy\s+policy|contact\s+us|customer\s+care)\b/i,
  /\b(?:fulfillment|nearest\s+pharmacy|delivery|licensed|retail|copyright|©|\u00a9)\b/i,
  /\b(?:javascript|window\.|document\.|function|var\s+|const\s+|let\s+|console\.)\b/i,   // JS code
  /<[a-z][^>]*>/i,                                    // HTML tags
  /\b(?:trustedsite|mcafee|google|facebook|twitter|instagram|linkedin|youtube)\b/i,     // brand spam
  /(?:\|.*){3,}/,                                     // pipe-delimited nav: "A | B | C | D"
  /[{}[\]]/,                                          // JSON/code brackets in a salt field
  /undefined/i,                                       // "undefined-undefinedundefined" concatenation artifacts
  /\b(?:which\s+is|that\s+is|it\s+is|belongs\s+to|is\s+a|is\s+an|is\s+used|helps\s+to|helps\s+in|works\s+by|known\s+as|often\s+included|supports?\s+bone|enhances?)\b/i, // description prose in salt
  /\b(?:tablet|capsule|syrup|injection|cream|ointment|gel|drops?|suspension|inhaler|spray|powder|lotion|solution|foam|respule)\s+(\d+mg|\d+ml|view|strip|bottle|tube|box)\b/i, // product name as salt
  /\b(?:View\s+Company|View\s+Feedback|Strip\s+Of|Bottle\s+Of|Tube\s+Of|Box\s+Of|Vial\s+Of|Packet\s+Of|Dry\s+Vial)\b/i,  // product listing noise
  /\bPharm\s*Easy\b/i,                                // PharmEasy site noise
];

// A valid salt should roughly look like "Chemical Name" or "Chemical + Chemical (Dosage)"
// This is a positive test: if salt passes garbage check, we still nuke it if it's too weird.
const SALT_MIN_LENGTH = 2;
const SALT_MAX_LENGTH = 250;

const GARBAGE_NAME_PATTERNS = [
  /https?:\/\//i,
  /www\.\w+/i,
  /<[a-z][^>]*>/i,
  /\b(?:javascript|window\.|document\.)\b/i,
  /(?:\|.*){3,}/,
  /[{}[\]]/,
];

// A name that is clearly just a description or page title stuffed into the name field
const NAME_IS_DESCRIPTION_REGEX = /^.{80,}$/;  // Names shouldn't be 80+ chars

// Normalized name / normalized_salt should never contain these
const NORMALIZED_GARBAGE_PATTERNS = [
  /https?:\/\//i,
  /www\.\w+/i,
  /<[a-z][^>]*>/i,
  /\b(?:order|buy|cart|shop|delivery|shipping|click|know\s+more|read\s+more)\b/i,
  /\b(?:fulfillment|pharmacy|licensed|retail|copyright)\b/i,
  /(?:\|.*){2,}/,
  /[{}[\]]/,
];

// ─── Helpers ────────────────────────────────────────────────────────
function isGarbage(value, patterns) {
  if (!value || typeof value !== "string") return true;
  const trimmed = value.trim();
  if (trimmed.length < SALT_MIN_LENGTH) return true;
  return patterns.some(p => p.test(trimmed));
}

function isSaltGarbage(salt, medicineName) {
  if (!salt) return true;
  const s = salt.trim();
  if (s.length < SALT_MIN_LENGTH || s.length > SALT_MAX_LENGTH) return true;
  if (GARBAGE_SALT_PATTERNS.some(p => p.test(s))) return true;
  // If the "salt" is almost entirely numeric (like a product ID)
  if (/^\d[\d\s.,]+$/.test(s)) return true;
  // If salt is just the medicine name repeated (with or without dosage appended)
  if (medicineName) {
    const normSalt = s.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
    const normName = medicineName.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
    if (normSalt === normName || normSalt.startsWith(normName + " ") || normName.startsWith(normSalt + " ")) {
      // Salt is just the brand name, not the chemical composition
      // Only flag if it doesn't contain a real salt-like pattern (e.g. contains "+" for combos)
      if (!s.includes("+") && !/\([^)]*\d+.*m[gl]\)/i.test(s)) {
        return true;
      }
    }
  }
  return false;
}

function isNormalizedGarbage(val) {
  if (!val) return false; // nothing to clean
  return NORMALIZED_GARBAGE_PATTERNS.some(p => p.test(val));
}

function isDescriptionGarbage(desc) {
  if (!desc || typeof desc !== "string") return false;
  const d = desc.trim();
  // Too short to be useful
  if (d.length < 15) return true;
  // Contains heavy HTML / JS residue
  if (/<[a-z][^>]*>/i.test(d)) return true;
  if (/\bfunction\b|\bvar\s|\bconsole\./i.test(d)) return true;
  // Is just a repeated product name (title stuffed as description)
  if (d.split(/\s+/).length <= 3) return true;
  return false;
}

// Try to derive a clean salt from salt_tokens
function deriveSaltFromTokens(tokens) {
  if (!Array.isArray(tokens) || tokens.length === 0) return null;
  const clean = tokens
    .filter(t => typeof t === "string" && t.trim().length >= 2)
    .map(t => t.trim());
  if (clean.length === 0) return null;
  // Capitalize each token properly
  return clean
    .map(t => t.charAt(0).toUpperCase() + t.slice(1))
    .join(" + ");
}

// Rebuild normalized_salt from salt field
function rebuildNormalizedSalt(salt) {
  if (!salt) return null;
  return salt
    .toLowerCase()
    .replace(/[^a-z0-9\s+]/g, " ")
    .replace(/\s+/g, " ")
    .trim() || null;
}

// Rebuild salt_tokens from salt field
function rebuildSaltTokens(salt) {
  if (!salt) return [];
  const tokens = salt
    .replace(/[()]/g, " ")
    .split(/\s*(?:\/|\+|,|&| along with | with | and )\s*/i)
    .map(t => t.toLowerCase().replace(/\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)/gi, "").replace(/[^a-z\s-]/g, " ").replace(/\s+/g, " ").trim())
    .filter(t => t.length >= 2);
  return [...new Set(tokens)];
}

// Rebuild primary_salt_key from salt_tokens
function rebuildPrimarySaltKey(tokens) {
  if (!Array.isArray(tokens) || tokens.length === 0) return null;
  return [...new Set(tokens)].sort().join("+");
}

// ─── Stats tracker ──────────────────────────────────────────────────
const stats = {
  totalScanned: 0,
  saltCleaned: 0,
  saltDerived: 0,
  saltNuked: 0,
  normalizedSaltFixed: 0,
  normalizedNameFixed: 0,
  descriptionCleaned: 0,
  descriptionNuked: 0,
  sideEffectsCleaned: 0,
  saltTokensRebuilt: 0,
  primarySaltKeyRebuilt: 0,
  nameTrimmed: 0,
  orphanPricesRemoved: 0,
  junkMedicinesRemoved: 0,
};

// ─── Main ───────────────────────────────────────────────────────────
async function run() {
  await connectDB();
  console.log(`\n🧹 Database Cleanup Script — ${APPLY ? "🔴 APPLY MODE" : "🟡 DRY RUN (use --apply to commit)"}\n`);

  const totalCount = await Medicine.countDocuments();
  console.log(`📦 Total medicines in DB: ${totalCount}\n`);

  const BATCH_SIZE = 500;
  let skip = 0;
  let processed = 0;

  while (skip < totalCount) {
    const batch = await Medicine.find({})
      .skip(skip)
      .limit(BATCH_SIZE)
      .lean();

    if (batch.length === 0) break;

    for (const med of batch) {
      processed++;
      const updates = {};
      const unsets = {};
      const problems = [];

      // ── 1. Clean salt field ──────────────────────────────────────
      if (med.salt && isSaltGarbage(med.salt, med.name)) {
        // Try to derive from salt_tokens first
        const derived = deriveSaltFromTokens(med.salt_tokens);
        if (derived && !isSaltGarbage(derived, med.name)) {
          updates.salt = derived;
          problems.push(`salt: garbage → derived from tokens: "${derived}"`);
          stats.saltCleaned++;
        } else {
          unsets.salt = 1;
          problems.push(`salt: nuked garbage: "${med.salt.substring(0, 80)}…"`);
          stats.saltNuked++;
        }
      }

      // ── 2. Fill missing salt ─────────────────────────────────────
      if (!med.salt && !updates.salt && !unsets.salt) {
        const derived = deriveSaltFromTokens(med.salt_tokens);
        if (derived && !isSaltGarbage(derived, med.name)) {
          updates.salt = derived;
          problems.push(`salt: missing → derived from tokens: "${derived}"`);
          stats.saltDerived++;
        }
      }

      // ── 3. Clean normalized_salt ─────────────────────────────────
      const effectiveSalt = updates.salt || (unsets.salt ? null : med.salt);
      if (med.normalized_salt && isNormalizedGarbage(med.normalized_salt)) {
        if (effectiveSalt) {
          updates.normalized_salt = rebuildNormalizedSalt(effectiveSalt);
        } else {
          unsets.normalized_salt = 1;
        }
        problems.push(`normalized_salt: cleaned garbage`);
        stats.normalizedSaltFixed++;
      } else if (effectiveSalt && !med.normalized_salt) {
        // Fill missing normalized_salt from clean salt
        updates.normalized_salt = rebuildNormalizedSalt(effectiveSalt);
        if (updates.normalized_salt) {
          problems.push(`normalized_salt: filled from salt`);
          stats.normalizedSaltFixed++;
        }
      }

      // ── 4. Clean normalized_name ─────────────────────────────────
      if (med.normalized_name && isNormalizedGarbage(med.normalized_name)) {
        // Rebuild from the name field
        const cleanNorm = med.name
          ?.toLowerCase()
          .replace(/[^\w\s-]/g, " ")
          .replace(/\s+/g, " ")
          .trim();
        if (cleanNorm) {
          updates.normalized_name = cleanNorm;
        }
        problems.push(`normalized_name: cleaned garbage`);
        stats.normalizedNameFixed++;
      }

      // ── 5. Clean description ─────────────────────────────────────
      if (med.description) {
        if (isDescriptionGarbage(med.description)) {
          unsets.description = 1;
          // Also clear llm_enriched flags so it gets re-enriched
          if (med.llm_enriched && med.llm_enriched_fields?.includes("description")) {
            updates.llm_enriched = false;
            updates.llm_enriched_fields = (med.llm_enriched_fields || []).filter(f => f !== "description");
          }
          problems.push(`description: nuked garbage (${med.description.length} chars)`);
          stats.descriptionNuked++;
        } else {
          const cleaned = cleanDescription(med.description);
          if (cleaned && cleaned !== med.description) {
            updates.description = cleaned;
            problems.push(`description: re-sanitized`);
            stats.descriptionCleaned++;
          }
        }
      }

      // ── 6. Clean side_effects ────────────────────────────────────
      if (Array.isArray(med.side_effects) && med.side_effects.length > 0) {
        const cleaned = cleanSideEffects(med.side_effects);
        if (cleaned.length !== med.side_effects.length ||
            JSON.stringify(cleaned) !== JSON.stringify(med.side_effects)) {
          if (cleaned.length > 0) {
            updates.side_effects = cleaned;
          } else {
            unsets.side_effects = 1;
            // Also reset LLM enrichment flag for side_effects
            if (med.llm_enriched && med.llm_enriched_fields?.includes("side_effects")) {
              updates.llm_enriched = false;
              updates.llm_enriched_fields = (updates.llm_enriched_fields || med.llm_enriched_fields || []).filter(f => f !== "side_effects");
            }
          }
          problems.push(`side_effects: re-cleaned (${med.side_effects.length} → ${cleaned.length})`);
          stats.sideEffectsCleaned++;
        }
      }

      // ── 7. Rebuild salt_tokens if we changed salt ────────────────
      const finalSalt = updates.salt || (unsets.salt ? null : med.salt);
      if (updates.salt || unsets.salt) {
        if (finalSalt) {
          const newTokens = rebuildSaltTokens(finalSalt);
          updates.salt_tokens = newTokens;
          updates.primary_salt_key = rebuildPrimarySaltKey(newTokens);
        } else {
          unsets.salt_tokens = 1;
          unsets.primary_salt_key = 1;
        }
        stats.saltTokensRebuilt++;
      }
      // Also fix missing primary_salt_key when salt_tokens exist
      if (!med.primary_salt_key && Array.isArray(med.salt_tokens) && med.salt_tokens.length > 0 && !unsets.primary_salt_key) {
        updates.primary_salt_key = rebuildPrimarySaltKey(med.salt_tokens);
        if (updates.primary_salt_key) {
          problems.push(`primary_salt_key: rebuilt from existing tokens`);
          stats.primarySaltKeyRebuilt++;
        }
      }

      // ── 8. Trim excessively long names ───────────────────────────
      if (med.name && med.name.length > 100) {
        // Likely a page title stuffed as name. Keep only the first meaningful part.
        const trimmed = med.name.substring(0, 80).replace(/\s+\S*$/, "").trim();
        if (trimmed.length >= 3 && trimmed !== med.name) {
          updates.name = trimmed;
          problems.push(`name: trimmed from ${med.name.length} chars`);
          stats.nameTrimmed++;
        }
      }

      // ── Apply changes ────────────────────────────────────────────
      if (Object.keys(updates).length > 0 || Object.keys(unsets).length > 0) {
        if (VERBOSE || !APPLY) {
          console.log(`  🩹 [${med._id}] ${med.name || "?"}`);
          for (const p of problems) {
            console.log(`     • ${p}`);
          }
        }
        if (APPLY) {
          const op = {};
          if (Object.keys(updates).length > 0) op.$set = updates;
          if (Object.keys(unsets).length > 0) op.$unset = unsets;
          await Medicine.updateOne({ _id: med._id }, op);
        }
      }

      stats.totalScanned++;
    }

    skip += BATCH_SIZE;
    process.stdout.write(`\r  Scanned ${processed}/${totalCount}...`);
  }

  // ── 9. Remove orphan prices ────────────────────────────────────
  console.log(`\n\n🔍 Checking for orphan prices...`);
  const allMedIds = await Medicine.distinct("_id");
  const medIdSet = new Set(allMedIds.map(id => id.toString()));
  const allPrices = await Price.find({}, { _id: 1, medicine_id: 1 }).lean();
  const orphanIds = [];
  for (const p of allPrices) {
    if (!medIdSet.has(p.medicine_id?.toString())) {
      orphanIds.push(p._id);
    }
  }
  if (orphanIds.length > 0) {
    console.log(`  Found ${orphanIds.length} orphan price documents.`);
    if (APPLY) {
      await Price.deleteMany({ _id: { $in: orphanIds } });
      console.log(`  ✅ Deleted ${orphanIds.length} orphan prices.`);
    }
    stats.orphanPricesRemoved = orphanIds.length;
  } else {
    console.log(`  ✅ No orphan prices found.`);
  }

  // ── 10. Remove junk medicine records ───────────────────────────
  console.log(`\n🔍 Checking for junk medicine records...`);
  const junkQuery = {
    $or: [
      { name: { $exists: false } },
      { name: "" },
      { name: { $regex: /^[\d\s.]+$/ } },            // purely numeric names
      { name: { $regex: /^https?:\/\//i } },           // URL as name
      { name: { $regex: /^<[a-z]/i } },                // HTML tag as name
    ]
  };
  const junkCount = await Medicine.countDocuments(junkQuery);
  if (junkCount > 0) {
    console.log(`  Found ${junkCount} junk medicine records.`);
    if (APPLY) {
      const junkMeds = await Medicine.find(junkQuery, { _id: 1 }).lean();
      const junkIds = junkMeds.map(m => m._id);
      // Delete their prices first
      await Price.deleteMany({ medicine_id: { $in: junkIds } });
      await Medicine.deleteMany({ _id: { $in: junkIds } });
      console.log(`  ✅ Deleted ${junkCount} junk medicines and their prices.`);
    }
    stats.junkMedicinesRemoved = junkCount;
  } else {
    console.log(`  ✅ No junk medicine records found.`);
  }

  // ── Summary ────────────────────────────────────────────────────
  console.log(`\n${"═".repeat(55)}`);
  console.log(`  📊 CLEANUP SUMMARY ${APPLY ? "(APPLIED)" : "(DRY RUN)"}`);
  console.log(`${"═".repeat(55)}`);
  console.log(`  Records scanned:        ${stats.totalScanned}`);
  console.log(`  ─── Salt ───`);
  console.log(`  Salt cleaned (garbage):  ${stats.saltCleaned}`);
  console.log(`  Salt derived (missing):  ${stats.saltDerived}`);
  console.log(`  Salt nuked (no fix):     ${stats.saltNuked}`);
  console.log(`  Salt tokens rebuilt:     ${stats.saltTokensRebuilt}`);
  console.log(`  Primary salt key fixed:  ${stats.primarySaltKeyRebuilt}`);
  console.log(`  ─── Normalized ───`);
  console.log(`  Normalized salt fixed:   ${stats.normalizedSaltFixed}`);
  console.log(`  Normalized name fixed:   ${stats.normalizedNameFixed}`);
  console.log(`  ─── Content ───`);
  console.log(`  Descriptions cleaned:    ${stats.descriptionCleaned}`);
  console.log(`  Descriptions nuked:      ${stats.descriptionNuked}`);
  console.log(`  Side effects cleaned:    ${stats.sideEffectsCleaned}`);
  console.log(`  Names trimmed:           ${stats.nameTrimmed}`);
  console.log(`  ─── Hygiene ───`);
  console.log(`  Orphan prices removed:   ${stats.orphanPricesRemoved}`);
  console.log(`  Junk medicines removed:  ${stats.junkMedicinesRemoved}`);
  console.log(`${"═".repeat(55)}\n`);

  if (!APPLY) {
    console.log(`  ⚠️  This was a DRY RUN. No changes were written.`);
    console.log(`  ➡️  Run with --apply to commit changes:\n`);
    console.log(`     node cleanup-db.js --apply\n`);
  }

  await mongoose.disconnect();
}

run().catch(err => {
  console.error("[Cleanup] Fatal error:", err);
  process.exit(1);
});
