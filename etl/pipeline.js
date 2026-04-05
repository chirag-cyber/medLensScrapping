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
    let rejectedCount = 0;

    for (const record of validRawRecords) {
      try {
        const transformedStruct = transformRecord(record);
        if (!transformedStruct.canonical_key) {
          rejectedCount++;
          console.warn(
            `[ETL SKIP] Missing canonical key for record: ${transformedStruct.raw_name}`
          );
          continue;
        }

        transformedRecords.push(transformedStruct);
      } catch (err) {
        rejectedCount++;
        console.error(`[ETL ERROR] Failed parsing: ${record.name}`, err);
      }
    }

    const loadSummary = await bulkUpsertProducts(transformedRecords, { batchSize });

    const summary = {
      extractedCount: validRawRecords.length,
      transformedCount: transformedRecords.length,
      transformRejectedCount: rejectedCount,
      strictRejectedCount: loadSummary.rejectedIncompleteMedicines || 0,
      rejectedCount: rejectedCount + (loadSummary.rejectedIncompleteMedicines || 0),
      medicinesTouched: loadSummary.medicinesTouched,
      priceEntriesUpserted: loadSummary.priceEntriesUpserted,
      incompleteMedicines: loadSummary.incompleteMedicines || [],
    };

    console.log("=== ETL Pipeline Completed ===");
    console.log(
      `Processed ${summary.transformedCount}/${summary.extractedCount} records. Medicines touched: ${summary.medicinesTouched}. Platform price rows upserted: ${summary.priceEntriesUpserted}. Rejected: ${summary.rejectedCount} (${summary.transformRejectedCount} transform + ${summary.strictRejectedCount} strict completeness).`
    );

    return summary;

  } catch (error) {
    console.error("[ETL FATAL ERROR]", error);
    throw error;
  }
}

module.exports = {
  runETL
};
