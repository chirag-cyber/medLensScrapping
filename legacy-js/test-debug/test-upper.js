require('dotenv').config({ path: '.env' });
const mongoose = require('mongoose');
const { Price } = require('./etl/load/models');

async function testUpper() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const result = await Price.find({ platform: { $regex: /[A-Z]/ } }).countDocuments();
  console.log('Prices with uppercase letters:', result);
  
  if (result > 0) {
    const samples = await Price.find({ platform: { $regex: /[A-Z]/ } }).limit(5);
    console.log('Samples:', samples);
  }
  process.exit(0);
}
testUpper();
