/**
 * Main ETL Orchestrator
 */

const connectDB = require("./load/db");
const { extractData } = require("./extract/extractor");
const { transformRecord } = require("./transform/transformer");
const { upsertProduct } = require("./load/upsert");

async function runETL(rawInputArray) {
  let db_connected = false;
  try {
    // 1. Setup DB Connection
    await connectDB();
    db_connected = true;

    // 2. Extract Phase (Validate shape)
    console.log("=== ETL Pipeline Started ===");
    const validRawRecords = extractData(rawInputArray);

    let processedCount = 0;
    
    // 3. Transform & 4. Load Phase (Stream processing element-by-element)
    for (const record of validRawRecords) {
      try {
        const transformedStruct = transformRecord(record);
        const savedId = await upsertProduct(transformedStruct);
        if (savedId) {
          processedCount++;
        }
      } catch (err) {
        console.error(`[ETL ERROR] Failed parsing: ${record.name}`, err);
      }
    }

    console.log(`=== ETL Pipeline Completed ===`);
    console.log(`Successfully merged/upserted ${processedCount} out of ${validRawRecords.length} records into Database.`);

  } catch (error) {
    console.error("[ETL FATAL ERROR]", error);
  }
}

module.exports = {
  runETL
};
