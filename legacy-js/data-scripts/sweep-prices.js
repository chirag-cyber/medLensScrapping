const mongoose = require('mongoose');
require('dotenv').config({ path: '.env' });
const { Price } = require('./etl/load/models');

async function sweepPrices() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  console.log('Connected to DB');

  const allPrices = await Price.find({});
  console.log(`Found ${allPrices.length} total prices`);

  const seen = new Set();
  let deletedCount = 0;

  for (const p of allPrices) {
    const key = p.medicine_id.toString() + '_' + p.platform.toLowerCase();
    if (seen.has(key)) {
      await Price.deleteOne({ _id: p._id });
      deletedCount++;
    } else {
      seen.add(key);
    }
  }

  console.log(`Deleted ${deletedCount} duplicate prices across the entire database.`);
  process.exit(0);
}

sweepPrices().catch(console.error);
