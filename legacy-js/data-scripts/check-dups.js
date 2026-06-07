require("dotenv").config({ path: ".env" });
const mongoose = require("mongoose");
const { Medicine, Price } = require("./etl/load/models");

async function fixDups() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
  console.log("Connected to MongoDB.");

  // Group by normalized_name
  const dups = await Medicine.aggregate([
    { $group: { _id: "$normalized_name", count: { $sum: 1 }, ids: { $push: "$_id" } } },
    { $match: { count: { $gt: 1 }, _id: { $ne: null } } }
  ]);
  
  console.log("Merging " + dups.length + " normalized_name duplicates...");
  let mergedGroups = 0;
  let totalPricesMoved = 0;
  let totalPricesDeleted = 0;
  let totalDuplicatesDeleted = 0;
  
  for (const group of dups) {
    const docs = await Medicine.find({ _id: { $in: group.ids } });
    if (docs.length <= 1) continue;
    
    // Pick master
    const master = docs.sort((a, b) => {
      if (a.is_canonical && !b.is_canonical) return -1;
      if (!a.is_canonical && b.is_canonical) return 1;
      const score = d => (d.dosage ? 1 : 0) + (d.image_url ? 1 : 0) + ((d.description && d.description.length > 20) ? 1 : 0);
      return score(b) - score(a);
    })[0];
    
    const duplicates = docs.filter(d => String(d._id) !== String(master._id));
    
    // Track platforms that the master already has
    const masterPrices = await Price.find({ medicine_id: master._id }, { platform: 1 }).lean();
    const masterPlatforms = new Set(masterPrices.map(p => p.platform));
    
    for (const dup of duplicates) {
      const dupPrices = await Price.find({ medicine_id: dup._id }).lean();
      
      for (const p of dupPrices) {
        if (masterPlatforms.has(p.platform)) {
          // Master already has a price for this platform, so delete the duplicate's price
          await Price.deleteOne({ _id: p._id });
          totalPricesDeleted++;
        } else {
          // Master doesn't have it, so we can move it
          await Price.updateOne({ _id: p._id }, { $set: { medicine_id: master._id } });
          masterPlatforms.add(p.platform);
          totalPricesMoved++;
        }
      }
      
      // Delete duplicate medicine
      await Medicine.deleteOne({ _id: dup._id });
      totalDuplicatesDeleted++;
    }
    
    mergedGroups++;
  }
  
  console.log("Finished merging " + mergedGroups + " groups.");
  console.log("Moved " + totalPricesMoved + " prices.");
  console.log("Deleted " + totalPricesDeleted + " redundant prices.");
  console.log("Deleted " + totalDuplicatesDeleted + " duplicate medicine records.");
  process.exit(0);
}
fixDups();
