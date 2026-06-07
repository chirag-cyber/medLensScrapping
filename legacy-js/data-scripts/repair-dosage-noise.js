const mongoose = require("mongoose");
require("dotenv").config();

async function repairCanonicalKeys() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("MONGO_URL not found.");
    process.exit(1);
  }

  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
  const Medicine = mongoose.model("Medicine", new mongoose.Schema({}, { strict: false }));

  console.log("Repairing canonical_keys with garbage dosages...");

  // Find all medicines whose canonical_key contains a garbage dosage pattern
  const garbageMeds = await Medicine.find({
    canonical_key: { $regex: /\|dose:\d{4,}%/ }
  });

  console.log(`Found ${garbageMeds.length} medicines with garbage canonical_keys`);

  let repaired = 0;
  let merged = 0;
  let errors = 0;

  for (const med of garbageMeds) {
    const newCanonicalKey = `name:${med.normalized_name}`;
    
    try {
      // Check if a clean record already exists with this canonical key
      const existing = await Medicine.findOne({ 
        canonical_key: newCanonicalKey, 
        _id: { $ne: med._id } 
      });

      if (existing) {
        // Merge platforms into the clean record and delete this one
        const mergedPlatforms = [...new Set([
          ...(existing.source_platforms || []), 
          ...(med.source_platforms || [])
        ])];
        await Medicine.updateOne(
          { _id: existing._id }, 
          { $set: { source_platforms: mergedPlatforms } }
        );
        await Medicine.deleteOne({ _id: med._id });
        merged++;
        console.log(`[Merged] "${med.name}" into existing clean record`);
      } else {
        // Just fix the canonical_key and clear the garbage dosage
        await Medicine.updateOne(
          { _id: med._id },
          { 
            $set: { canonical_key: newCanonicalKey },
            $unset: { dosage: "" }
          }
        );
        repaired++;
      }
    } catch (err) {
      if (err.code === 11000) {
        // Duplicate normalized_name+dosage — merge into existing
        try {
          const cleanMed = await Medicine.findOne({ 
            normalized_name: med.normalized_name, 
            _id: { $ne: med._id } 
          });
          if (cleanMed) {
            const mergedPlatforms = [...new Set([
              ...(cleanMed.source_platforms || []), 
              ...(med.source_platforms || [])
            ])];
            await Medicine.updateOne(
              { _id: cleanMed._id }, 
              { $set: { source_platforms: mergedPlatforms } }
            );
            await Medicine.deleteOne({ _id: med._id });
            merged++;
            console.log(`[Collision Merged] "${med.name}"`);
          }
        } catch (innerErr) {
          console.error(`Failed to merge "${med.name}": ${innerErr.message}`);
          errors++;
        }
      } else {
        console.error(`Failed "${med.name}": ${err.message}`);
        errors++;
      }
    }
  }

  console.log(`\nRepair complete!`);
  console.log(`  Repaired: ${repaired}`);
  console.log(`  Merged: ${merged}`);
  console.log(`  Errors: ${errors}`);
  
  await mongoose.disconnect();
}

repairCanonicalKeys().catch(console.error);
