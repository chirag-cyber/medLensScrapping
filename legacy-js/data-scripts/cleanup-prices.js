require("dotenv").config({ path: ".env" });
const mongoose = require("mongoose");
const { Price } = require("./etl/load/models");

async function cleanupPrices() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
  console.log("Connected to MongoDB.");

  // Group prices by medicine_id and platform
  const dups = await Price.aggregate([
    { $group: { _id: { medicine_id: "$medicine_id", platform: "$platform" }, count: { $sum: 1 }, docs: { $push: "$_id" } } },
    { $match: { count: { $gt: 1 } } }
  ]);
  
  console.log("Duplicate price groups found:", dups.length);
  
  let deletedCount = 0;
  for (const group of dups) {
    // Keep the first one, delete the rest
    const docsToDelete = group.docs.slice(1);
    await Price.deleteMany({ _id: { $in: docsToDelete } });
    deletedCount += docsToDelete.length;
  }
  
  console.log(`Deleted ${deletedCount} duplicate prices.`);
  process.exit(0);
}
cleanupPrices();
