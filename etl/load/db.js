const mongoose = require("mongoose");
require("dotenv").config();
const { Medicine, Price } = require("./models");

function hasMatchingTextWeights(index, expectedWeights) {
  const actualWeights = index?.weights || {};
  const expectedEntries = Object.entries(expectedWeights);

  if (Object.keys(actualWeights).length !== expectedEntries.length) {
    return false;
  }

  return expectedEntries.every(
    ([field, weight]) => actualWeights[field] === weight
  );
}

async function ensureMedicineIndexes() {
  try {
    await Medicine.collection.updateMany(
      {
        $or: [
          { canonical_key: null },
          { canonical_key: "" },
        ],
      },
      {
        $unset: { canonical_key: 1 },
      }
    );

    const existingIndexes = await Medicine.collection.indexes();
    const canonicalIndex = existingIndexes.find((index) => index.name === "canonical_key_1");
    const desiredTextIndexName = "medicine_text_search";
    const desiredTextWeights = {
      normalized_name: 1,
      salt: 1,
      description: 1,
    };

    if (
      canonicalIndex &&
      !canonicalIndex.partialFilterExpression
    ) {
      console.warn(
        "[MongoDB] Replacing legacy canonical_key_1 index with partial unique index."
      );
      await Medicine.collection.dropIndex("canonical_key_1");
    }

    const existingTextIndexes = existingIndexes.filter(
      (index) => index.weights && index.key?._fts === "text"
    );

    for (const textIndex of existingTextIndexes) {
      const isExpectedIndex =
        textIndex.name === desiredTextIndexName &&
        hasMatchingTextWeights(textIndex, desiredTextWeights);

      if (!isExpectedIndex) {
        console.warn(
          `[MongoDB] Replacing legacy text index ${textIndex.name} with ${desiredTextIndexName}.`
        );
        await Medicine.collection.dropIndex(textIndex.name);
      }
    }
  } catch (error) {
    if (
      !String(error.message || "").toLowerCase().includes("ns does not exist") &&
      !String(error.message || "").toLowerCase().includes("namespace")
    ) {
      throw error;
    }
  }
}

async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  
  if (!MONGO_URI) {
    console.error(`\n[MongoDB Error] CRITICAL: MONGO_URL is missing in your .env file!`);
    process.exit(1);
  }

  try {
    if (mongoose.connection.readyState === 1) return; // Already connected
    
    // Explicitly define the database name as MEDSAVE
    await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
    await ensureMedicineIndexes();
    await Promise.all([Medicine.createIndexes(), Price.createIndexes()]);
    console.log("[MongoDB] Connected successfully to MEDSAVE Database.");
  } catch (error) {
    console.error("[MongoDB Error] Failed to connect:", error.message);
    process.exit(1);
  }
}

module.exports = connectDB;
