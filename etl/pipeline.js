/**
 * Main ETL Orchestrator
 */

const connectDB = require("./load/db");
const { extractData } = require("./extract/extractor");
const { transformRecord } = require("./transform/transformer");
const { bulkUpsertProducts } = require("./load/upsert");

async function runETL(rawInputArray, options = {}) {
  const { batchSize = 25 } = options;

  try {
    await connectDB();

    const validRawRecords = extractData(rawInputArray);
    const transformedRecords = [];
    let transformRejectedCount = 0;
    let nameQualityRejectedCount = 0;
    const nameQualityRejections = [];

    for (const record of validRawRecords) {
      try {
        const transformedStruct = transformRecord(record);

        if (!transformedStruct.canonical_key) {
          transformRejectedCount++;
          continue;
        }

        // Track name quality issues (but don't reject here — let completeness check handle it)
        if (transformedStruct.cleaned_name === null && transformedStruct.raw_name !== "Unknown Product") {
          nameQualityRejectedCount++;
          nameQualityRejections.push({
            raw_name: transformedStruct.raw_name,
            platform: transformedStruct.platform,
            reason: "Name failed SEO/quality validation",
          });
        }

        transformedRecords.push(transformedStruct);
      } catch (err) {
        transformRejectedCount++;
      }
    }

    const loadSummary = await bulkUpsertProducts(transformedRecords, { batchSize });

    const totalRejected = transformRejectedCount + (loadSummary.rejectedIncompleteMedicines || 0);

    const summary = {
      extractedCount: validRawRecords.length,
      transformedCount: transformedRecords.length,
      transformRejectedCount,
      nameQualityRejectedCount,
      strictRejectedCount: loadSummary.rejectedIncompleteMedicines || 0,
      rejectedCount: totalRejected,
      medicinesTouched: loadSummary.medicinesTouched,
      priceEntriesUpserted: loadSummary.priceEntriesUpserted,
      incompleteMedicines: loadSummary.incompleteMedicines || [],
      nameQualityRejections: nameQualityRejections.slice(0, 20), // cap logged rejections
    };

    if (nameQualityRejections.length > 0) {
      nameQualityRejections.slice(0, 5).forEach((r) =>
      );
    }

    return summary;

  } catch (error) {
    throw error;
  }
}

module.exports = {
  runETL
};
