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
    console.log("=== ETL Pipeline Started ===");

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
          console.warn(
            `[ETL SKIP] Missing canonical key for record: ${transformedStruct.raw_name}`
          );
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
        console.error(`[ETL ERROR] Failed parsing: ${record.name}`, err);
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

    console.log("=== ETL Pipeline Completed ===");
    console.log(
      `Processed ${summary.transformedCount}/${summary.extractedCount} records. ` +
      `Medicines touched: ${summary.medicinesTouched}. ` +
      `Price rows upserted: ${summary.priceEntriesUpserted}. ` +
      `Rejected: ${totalRejected} (${transformRejectedCount} transform + ${summary.strictRejectedCount} strict). ` +
      `Name quality warnings: ${nameQualityRejectedCount}.`
    );

    if (nameQualityRejections.length > 0) {
      console.log(`[DATA QUALITY] Sample name rejections:`);
      nameQualityRejections.slice(0, 5).forEach((r) =>
        console.log(`  ✗ "${r.raw_name}" (${r.platform}) — ${r.reason}`)
      );
    }

    return summary;

  } catch (error) {
    console.error("[ETL FATAL ERROR]", error);
    throw error;
  }
}

module.exports = {
  runETL
};
