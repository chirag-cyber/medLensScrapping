const mongoose = require("mongoose");
require("dotenv").config();

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
    console.log("[MongoDB] Connected successfully to MEDSAVE Database.");
  } catch (error) {
    console.error("[MongoDB Error] Failed to connect:", error.message);
    process.exit(1);
  }
}

module.exports = connectDB;
