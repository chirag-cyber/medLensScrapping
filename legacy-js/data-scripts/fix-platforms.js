require("dotenv").config({ path: ".env" });
const mongoose = require("mongoose");
const { Price } = require("./etl/load/models");

async function fixPlatformCase() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
  console.log("Connected to MongoDB.");

  // Update all platforms to lowercase
  const prices = await Price.find({});
  let caseFixedCount = 0;
  for (const p of prices) {
    if (p.platform !== p.platform.toLowerCase()) {
      p.platform = p.platform.toLowerCase();
      // Wait, since there is a unique index on medicine_id + platform,
      // saving this directly might throw a duplicate key error if the lowercase version already exists!
      try {
        await p.save();
        caseFixedCount++;
      } catch (err) {
        if (err.code === 11000) {
          // If the lowercase version already exists, this one is a duplicate! We can safely delete it.
          await Price.deleteOne({ _id: p._id });
        }
      }
    }
  }

  console.log("Fixed case for " + caseFixedCount + " prices (and deleted duplicates where applicable).");
  
  // Now run deduplication grouping by normalized platform
  const dups = await Price.aggregate([
    { $group: { _id: { medicine_id: "$medicine_id", platform: "$platform" }, count: { $sum: 1 }, docs: { $push: "$_id" } } },
    { $match: { count: { $gt: 1 } } }
  ]);
  
  console.log("Duplicate price groups found after lowercasing:", dups.length);
  let deletedCount = 0;
  for (const group of dups) {
    const docsToDelete = group.docs.slice(1);
    await Price.deleteMany({ _id: { $in: docsToDelete } });
    deletedCount += docsToDelete.length;
  }
  
  console.log(`Deleted ${deletedCount} duplicate prices.`);
  process.exit(0);
}

fixPlatformCase();
