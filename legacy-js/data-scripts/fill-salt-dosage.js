#!/usr/bin/env node
/**
 * Fill Missing Salt & Dosage — fill-salt-dosage.js
 *
 * Two-pass approach:
 *   Pass 1 (FREE): Extract dosage from medicine name using regex
 *   Pass 2 (LLM):  Use Groq to look up missing salt (composition) from name + manufacturer
 *
 * Usage:
 *   node fill-salt-dosage.js                    # dry run
 *   node fill-salt-dosage.js --apply            # write changes
 *   node fill-salt-dosage.js --apply --limit 50 # limit LLM calls
 *   node fill-salt-dosage.js --apply --dosage-only  # skip LLM, only extract dosage
 */

require("dotenv").config();
const mongoose = require("mongoose");
const axios = require("axios");
const connectDB = require("./etl/load/db");
const { Medicine } = require("./etl/load/models");
const { getRotator } = require("./etl/enrich/llmClient");

// ─── CLI ────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const APPLY       = args.includes("--apply");
const DOSAGE_ONLY = args.includes("--dosage-only");
const VERBOSE     = args.includes("--verbose");
const limitIdx    = args.indexOf("--limit");
const LLM_LIMIT   = limitIdx > -1 ? parseInt(args[limitIdx + 1], 10) : 100;
const delayIdx    = args.indexOf("--delay");
const DELAY_MS    = delayIdx > -1 ? parseInt(args[delayIdx + 1], 10) : 2500;

// ─── Groq Config ────────────────────────────────────────────────────
const GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_MODEL   = "llama-3.3-70b-versatile";

// ─── Dosage extraction (regex — free, no API cost) ──────────────────
const DOSAGE_REGEX =
  /((?:\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)?\s*(?:\+|and|&|\/|-)\s*)*\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%))(?!\w)/i;

function extractDosageFromName(name) {
  if (!name) return null;
  const match = name.match(DOSAGE_REGEX);
  return match ? match[1].trim() : null;
}

// ─── Salt lookup via LLM ────────────────────────────────────────────
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function buildSaltLookupPrompt(medicine) {
  const parts = [
    `Medicine name: "${medicine.name}"`,
    medicine.manufacturer ? `Manufacturer: ${medicine.manufacturer}` : null,
    medicine.dosage ? `Dosage: ${medicine.dosage}` : null,
  ].filter(Boolean).join("\n");

  return `${parts}

What is the active ingredient / salt composition of this medicine?

Return JSON only:
{
  "salt": "Active Ingredient Name (dosage if known)",
  "dosage": "${medicine.dosage || 'extract from name if possible'}"
}

RULES:
- JSON only, no markdown.
- "salt" must be the chemical/generic name (e.g. "Paracetamol", "Atorvastatin (20mg)", "Metformin + Sitagliptin").
- Do NOT return the brand name as salt.
- If you genuinely don't know, return {"salt": null, "dosage": null}.
- Be factual. This is for a medical database.`;
}

async function lookupSaltViaLLM(medicine, rotator) {
  const prompt = buildSaltLookupPrompt(medicine);
  const maxRetries = rotator.keys.length;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const keyEntry = rotator.getKey();

    // If all keys are in cooldown, wait for the soonest one
    if (keyEntry.waitMs) {
      await new Promise(r => setTimeout(r, keyEntry.waitMs));
    }

    try {
      const response = await axios.post(
        GROQ_API_URL,
        {
          model: GROQ_MODEL,
          messages: [
            { role: "system", content: "You are a pharmaceutical database assistant. Return JSON only." },
            { role: "user", content: prompt },
          ],
          temperature: 0.1,
          max_tokens: 256,
          response_format: { type: "json_object" },
        },
        {
          headers: {
            Authorization: `Bearer ${keyEntry.key}`,
            "Content-Type": "application/json",
          },
          timeout: 30000,
        }
      );

      const rawContent = response.data?.choices?.[0]?.message?.content;
      if (!rawContent) return null;

      // Parse JSON
      let text = rawContent.trim();
      text = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```\s*$/, "");

      try {
        const parsed = JSON.parse(text);
        return parsed;
      } catch {
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          try { return JSON.parse(jsonMatch[0]); } catch { return null; }
        }
        return null;
      }
    } catch (error) {
      const statusCode = error.response?.status;
      if (statusCode === 429) {
        const retryAfterSec = parseInt(error.response?.headers?.["retry-after"], 10);
        const cooldownMs = retryAfterSec ? retryAfterSec * 1000 : 60000;
        rotator.markRateLimited(keyEntry, cooldownMs);

        if (attempt < maxRetries) {
          continue;
        }

        return { _retryable: true, _error: "All keys rate limited" };
      }
      return { _error: error.message || "Unknown error" };
    }
  }
}

// Validate that the returned salt isn't just the brand name again
function isValidSalt(salt, medicineName) {
  if (!salt || typeof salt !== "string") return false;
  const s = salt.trim();
  if (s.length < 3 || s.length > 300) return false;
  if (/null/i.test(s) && s.length < 10) return false;
  if (/undefined/i.test(s)) return false;
  // Don't accept if it's just the brand name
  const normSalt = s.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  const normName = (medicineName || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim()
    .replace(/\s*\d+\s*(?:mg|ml|mcg|gm?|kg|%)\s*/g, " ").replace(/\b(?:tablet|capsule|syrup|injection|cream|gel|drops?|sr|xr|er|od|dt|md|forte|plus)\b/g, " ")
    .replace(/\s+/g, " ").trim();
  if (normSalt === normName) return false;
  return true;
}

// Rebuild normalized_salt / salt_tokens / primary_salt_key from a clean salt
function rebuildSaltMeta(salt) {
  const normalized_salt = salt.toLowerCase()
    .replace(/[^a-z0-9\s+]/g, " ")
    .replace(/\s+/g, " ").trim() || null;

  const salt_tokens = salt
    .replace(/[()]/g, " ")
    .split(/\s*(?:\/|\+|,|&| along with | with | and )\s*/i)
    .map(t => t.toLowerCase()
      .replace(/\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)/gi, "")
      .replace(/[^a-z\s-]/g, " ")
      .replace(/\s+/g, " ").trim())
    .filter(t => t.length >= 2);
  const uniqueTokens = [...new Set(salt_tokens)];

  const primary_salt_key = uniqueTokens.length > 0
    ? uniqueTokens.sort().join("+")
    : null;

  return { normalized_salt, salt_tokens: uniqueTokens, primary_salt_key };
}

// ─── Main ───────────────────────────────────────────────────────────
async function run() {
  await connectDB();
  console.log(`\n💊 Fill Missing Salt & Dosage — ${APPLY ? "🔴 APPLY MODE" : "🟡 DRY RUN"}\n`);

  // ═══════════════════════════════════════════════════════════════════
  // PASS 1: Extract dosage from medicine names (FREE — no API cost)
  // ═══════════════════════════════════════════════════════════════════
  console.log("━".repeat(55));
  console.log("  PASS 1: Extract dosage from medicine names (free)");
  console.log("━".repeat(55));

  const noDosage = await Medicine.find({
    $or: [
      { dosage: { $exists: false } },
      { dosage: null },
      { dosage: "" },
    ]
  }).lean();

  console.log(`  Found ${noDosage.length} medicines without dosage.\n`);
  let dosageFixed = 0;

  for (const med of noDosage) {
    const extracted = extractDosageFromName(med.name);
    if (extracted) {
      dosageFixed++;
      if (VERBOSE) {
        console.log(`  ✅ [${med.name}] → dosage: ${extracted}`);
      }
      if (APPLY) {
        try {
          await Medicine.updateOne({ _id: med._id }, { $set: { dosage: extracted } });
        } catch (err) {
          if (err.code === 11000) {
            // Duplicate key: another record already has this normalized_name + dosage
            // Skip silently — the interlink script will merge these later
            if (VERBOSE) console.log(`  ⚠️  [${med.name}] skipped dosage (duplicate key conflict)`);
            dosageFixed--; // undo the count
          } else {
            throw err;
          }
        }
      }
    }
  }

  console.log(`  📊 Dosage extracted from names: ${dosageFixed} / ${noDosage.length}`);
  console.log(`  ❌ Still missing dosage: ${noDosage.length - dosageFixed}\n`);

  if (DOSAGE_ONLY) {
    console.log("  --dosage-only flag set. Skipping LLM salt lookup.\n");
    await mongoose.disconnect();
    return;
  }

  // ═══════════════════════════════════════════════════════════════════
  // PASS 2: LLM lookup for missing salt (uses Groq API)
  // ═══════════════════════════════════════════════════════════════════
  console.log("━".repeat(55));
  console.log("  PASS 2: LLM salt lookup via Groq (costs API tokens)");
  console.log("━".repeat(55));

  const apiKeys = [
    process.env.GROQ_API_KEY,
    process.env.GROQ_API_KEY_2,
    process.env.GROQ_API_KEY_3,
    process.env.GROQ_API_KEY_4,
    process.env.GROQ_API_KEY_5,
    process.env.GROQ_API_KEY_6,
    process.env.GROQ_API_KEY_7,
    process.env.GROQ_API_KEY_8,
    process.env.GROQ_API_KEY_9,
    process.env.GROQ_API_KEY_10,
    process.env.GROQ_API_KEY_11,
  ].filter(Boolean);

  if (apiKeys.length === 0) {
    console.error("  ⚠️  No GROQ_API_KEY found in .env. Skipping salt lookup.");
    await mongoose.disconnect();
    return;
  }

  const rotator = getRotator(apiKeys);

  const noSalt = await Medicine.find({
    $or: [
      { salt: { $exists: false } },
      { salt: null },
      { salt: "" },
    ]
  }).limit(LLM_LIMIT).lean();

  console.log(`  Found ${noSalt.length} medicines without salt (limit: ${LLM_LIMIT}).\n`);

  let saltFilled = 0;
  let saltFailed = 0;
  let dosageFilledByLLM = 0;
  const errors = [];

  for (let i = 0; i < noSalt.length; i++) {
    const med = noSalt[i];
    process.stdout.write(`\r  [${i + 1}/${noSalt.length}] Looking up: ${(med.name || "?").substring(0, 40).padEnd(40)}...`);

    const result = await lookupSaltViaLLM(med, rotator);

    if (!result || result._error) {
      saltFailed++;
      errors.push({ name: med.name, error: result?._error || "No response" });
      if (result?._retryable) {
        console.log(`\n  ⏳ All keys rate limited. Backing off...`);
        await sleep(DELAY_MS * 3);
      }
      continue;
    }

    const updates = {};
    let changed = false;

    // Salt
    if (result.salt && isValidSalt(result.salt, med.name)) {
      updates.salt = result.salt.trim();
      const meta = rebuildSaltMeta(updates.salt);
      updates.normalized_salt = meta.normalized_salt;
      updates.salt_tokens = meta.salt_tokens;
      updates.primary_salt_key = meta.primary_salt_key;
      saltFilled++;
      changed = true;
    }

    // Dosage (fill from LLM if still missing)
    if (!med.dosage && result.dosage && typeof result.dosage === "string" && result.dosage.trim() !== "null") {
      updates.dosage = result.dosage.trim();
      dosageFilledByLLM++;
      changed = true;
    }

    if (changed && APPLY) {
      try {
        await Medicine.updateOne({ _id: med._id }, { $set: updates });
      } catch (err) {
        if (err.code === 11000) {
          // Duplicate key: adding dosage would conflict with an existing record
          // Try again without dosage update
          if (updates.dosage) {
            delete updates.dosage;
            dosageFilledByLLM--;
            try {
              if (Object.keys(updates).length > 0) {
                await Medicine.updateOne({ _id: med._id }, { $set: updates });
              }
            } catch (retryErr) {
              if (retryErr.code === 11000) {
                if (VERBOSE) console.log(`\n  ⚠️  [${med.name}] skipped (duplicate key conflict)`);
              } else {
                throw retryErr;
              }
            }
          } else {
            if (VERBOSE) console.log(`\n  ⚠️  [${med.name}] skipped (duplicate key conflict)`);
          }
        } else {
          throw err;
        }
      }
    }

    if (VERBOSE && changed) {
      console.log(`\n  ✅ [${med.name}] salt: "${updates.salt || "—"}" | dosage: "${updates.dosage || med.dosage || "—"}"`);
    }

    // Rate limit pacing
    if (i < noSalt.length - 1) {
      await sleep(DELAY_MS);
    }
  }

  // ─── Summary ──────────────────────────────────────────────────────
  console.log(`\n\n${"═".repeat(55)}`);
  console.log(`  📊 FILL SUMMARY ${APPLY ? "(APPLIED)" : "(DRY RUN)"}`);
  console.log(`${"═".repeat(55)}`);
  console.log(`  ─── Pass 1: Dosage (regex) ───`);
  console.log(`  Medicines without dosage:   ${noDosage.length}`);
  console.log(`  Dosage extracted from name:  ${dosageFixed}`);
  console.log(`  ─── Pass 2: Salt (LLM) ───`);
  console.log(`  Medicines without salt:      ${noSalt.length}`);
  console.log(`  Salt filled by LLM:          ${saltFilled}`);
  console.log(`  Dosage filled by LLM:        ${dosageFilledByLLM}`);
  console.log(`  LLM failures:                ${saltFailed}`);
  console.log(`${"═".repeat(55)}\n`);

  if (errors.length > 0 && VERBOSE) {
    console.log("  Errors:");
    errors.forEach(e => console.log(`    • ${e.name}: ${e.error}`));
  }

  if (!APPLY) {
    console.log(`  ⚠️  DRY RUN — no changes written.`);
    console.log(`  ➡️  Run with --apply to commit:\n`);
    console.log(`     node fill-salt-dosage.js --apply --limit ${LLM_LIMIT}\n`);
  }

  await mongoose.disconnect();
}

run().catch(err => {
  console.error("[Fill Salt/Dosage] Fatal:", err);
  process.exit(1);
});
