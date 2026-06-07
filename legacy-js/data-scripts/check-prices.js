const mongoose = require('mongoose');
require('dotenv').config({ path: '.env' });
const { Price } = require('./etl/load/models');

async function test() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const platformGroups = await Price.aggregate([
    { $group: { _id: { med: '$medicine_id', plat: { $toLower: '$platform' } }, count: { $sum: 1 } } },
    { $match: { count: { $gt: 1 } } }
  ]);
  console.log('Duplicate prices (same medicine, same lowercased platform):', platformGroups.length);
  if (platformGroups.length > 0) {
    console.log('Sample duplicate:', platformGroups[0]);
  }
  process.exit(0);
}
test();
