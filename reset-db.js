require("dotenv").config();
const mongoose = require("mongoose");
const connectDB = require("./etl/load/db");
const { Medicine, Price } = require("./etl/load/models");

async function resetDB() {
  try {
    await connectDB();
    console.log("=== DB Reset Started ===");

    const medResult = await Medicine.deleteMany({});
    console.log(`Deleted ${medResult.deletedCount} records from medicines collection.`);

    const priceResult = await Price.deleteMany({});
    console.log(`Deleted ${priceResult.deletedCount} records from prices collection.`);

    console.log("=== DB Reset Completed ===");
  } catch (err) {
    console.error("Reset failed due to error:", err);
  } finally {
    mongoose.disconnect();
  }
}

resetDB();
