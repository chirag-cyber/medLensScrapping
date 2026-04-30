const mongoose = require('mongoose');
require('dotenv').config({ path: require('path').resolve(__dirname, '.env') });

async function checkDb() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
  const Medicine = mongoose.model('Medicine', new mongoose.Schema({}, { strict: false }));
  const Price = mongoose.model('Price', new mongoose.Schema({}, { strict: false }));

  const medCount = await Medicine.countDocuments();
  const priceCount = await Price.countDocuments();
  console.log('Total Medicines:', medCount);
  console.log('Total Prices:', priceCount);

  // Find all medicines that contain 'paracetamol' or are just all medicines
  const pricesPerMed = await Price.aggregate([
    { $group: { _id: '$medicine_id', count: { $sum: 1 }, platforms: { $push: '$platform' } } },
    { $sort: { count: -1 } }
  ]);

  console.log(`\nPrice distribution:`);
  console.log(`Medicines with >1 price:`, pricesPerMed.filter(p => p.count > 1).length);
  console.log(`Medicines with exactly 1 price:`, pricesPerMed.filter(p => p.count === 1).length);

  if (pricesPerMed.filter(p => p.count > 1).length > 0) {
     const topInterlinkedId = pricesPerMed.find(p => p.count > 1)._id;
     const sampleMed = await Medicine.findById(topInterlinkedId).lean();
     console.log(`\nSample Interlinked Medicine:`, sampleMed.name, '-> Platforms:', pricesPerMed.find(p => p.count > 1).platforms);
  }

  // Group medicines by their cleaned base name to see why they aren't merging
  const duplicates = await Medicine.aggregate([
    { $group: { 
        _id: '$normalized_name', 
        count: { $sum: 1 }, 
        docs: { $push: { name: '$name', dosage: '$dosage', platform: '$source_platforms' } } 
    } },
    { $match: { count: { $gt: 1 } } },
    { $sort: { count: -1 } },
    { $limit: 3 }
  ]);
  
  console.log('\nMedicines with the SAME normalized_name that FAILED to merge (different dosages?):', JSON.stringify(duplicates, null, 2));

  mongoose.disconnect();
}
checkDb();
