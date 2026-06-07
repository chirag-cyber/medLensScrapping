/**
 * LLM Enrichment Engine
 *
 * Orchestrates batch enrichment of medicines using the LLM Client.
 * Handles querying MongoDB for incomplete records, pacing requests
 * to avoid Groq rate limits, and persisting the enriched data.
 */

const { Medicine } = require("../load/models");
const { enrichMedicine, detectMissingFields } = require("./llmClient");

// Wait between requests to respect Groq free tier (30 RPM)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Query MongoDB for medicines that are missing one or more enrichable fields.
 *
 * @param {object} options
 * @param {number} options.limit - Max records to fetch
 * @param {boolean} options.forceReenrich - If true, ignores llm_enriched flag
 * @returns {Promise<Array>} Array of medicine documents
 */
async function findIncompleteMedicines(options = {}) {
  const { limit = 50, forceReenrich = false } = options;

  // Build query to find records missing any of the key fields
  const missingFieldConditions = [
    { description: { $exists: false } },
    { description: "" },
    { description: null },
    { "side_effects.0": { $exists: false } },
    { "faq.0": { $exists: false } },
  ];

  const query = {
    $or: missingFieldConditions,
  };

  if (!forceReenrich) {
    query.llm_enriched = { $ne: true };
  }

  // Find incomplete medicines
  return await Medicine.find(query)
    .limit(limit)
    .sort({ createdAt: 1 })
    .exec();
}

/**
 * Enrich a batch of medicines and strictly respect API rate limits.
 *
 * @param {Array} medicines - Medicines to enrich
 * @param {object} options
 * @param {string} options.apiKey - Groq API key
 * @param {boolean} options.dryRun - If true, do not save to DB
 * @param {number} options.delayMs - Delay between LLM calls
 * @param {Function} options.onProgress - Optional progress callback
 */
async function enrichBatch(medicines, options = {}) {
  const { apiKey, dryRun = false, delayMs = 2500, onProgress } = options;

  const results = {
    totalProcessed: 0,
    successfulUpdates: 0,
    failedUpdates: 0,
    skipped: 0,
    fieldsEnrichedCounts: {},
    errors: [],
  };

  for (let i = 0; i < medicines.length; i++) {
    const medicine = medicines[i];
    results.totalProcessed++;

    if (onProgress) {
      onProgress(i + 1, medicines.length, medicine);
    }

    // Safety check BEFORE making an API call: Does it actually need anything?
    const missingFields = detectMissingFields(medicine);

    if (missingFields.length === 0) {
      results.skipped++;
      if (!dryRun) {
        // Just mark it as "enriched" so we don't pick it up again if it was manually fixed
        medicine.llm_enriched = true;
        medicine.llm_enriched_at = new Date();
        await medicine.save();
      }
      continue;
    }

    try {
      const enrichmentResult = await enrichMedicine(medicine, { apiKey, missingFields });

      if (enrichmentResult.success) {
        const { enrichedFields, fieldsUpdated } = enrichmentResult;

        // Track stats
        results.successfulUpdates++;
        fieldsUpdated.forEach((field) => {
          results.fieldsEnrichedCounts[field] = (results.fieldsEnrichedCounts[field] || 0) + 1;
        });

        if (!dryRun) {
          // Apply updates
          Object.assign(medicine, enrichedFields);
          medicine.llm_enriched = true;
          medicine.llm_enriched_at = new Date();

          // Append to llm_enriched_fields list
          medicine.llm_enriched_fields = [
            ...(medicine.llm_enriched_fields || []),
            ...fieldsUpdated
          ];
          // deduplicate just in case
          medicine.llm_enriched_fields = [...new Set(medicine.llm_enriched_fields)];

          await medicine.save();
        }
      } else {
        results.failedUpdates++;
        results.errors.push({ id: medicine._id, name: medicine.name, error: enrichmentResult.error });

        if (enrichmentResult.retryable) {
          // Backoff extra if rate limited
          await sleep(delayMs * 2);
        }
      }
    } catch (err) {
      results.failedUpdates++;
      results.errors.push({ id: medicine._id, name: medicine.name, error: err.message });
    }

    // Delay to respect rate limits (except on the last item)
    if (i < medicines.length - 1) {
      await sleep(delayMs);
    }
  }

  return results;
}

module.exports = {
  findIncompleteMedicines,
  enrichBatch,
};
