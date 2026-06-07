require('dotenv').config({ path: '.env' });
const mongoose = require('mongoose');
const { Price } = require('./etl/load/models');

async function forceLower() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  console.log('Connected to MongoDB.');

  const cursor = mongoose.connection.db.collection('prices').find({ platform: { $regex: /[A-Z]/ } });
  const docs = await cursor.toArray();
  console.log(`Found ${docs.length} prices with uppercase platforms.`);

  let updatedCount = 0;
  let deletedCount = 0;

  for (const doc of docs) {
    const lowerPlatform = doc.platform.toLowerCase();
    
    // Check if the lowercase version already exists for this medicine
    const existing = await mongoose.connection.db.collection('prices').findOne({
      medicine_id: doc.medicine_id,
      platform: lowerPlatform
    });

    if (existing) {
      // If it exists, this uppercase one is a duplicate, just delete it
      await mongoose.connection.db.collection('prices').deleteOne({ _id: doc._id });
      deletedCount++;
    } else {
      // Otherwise, update it to lowercase
      await mongoose.connection.db.collection('prices').updateOne(
        { _id: doc._id },
        { $set: { platform: lowerPlatform } }
      );
      updatedCount++;
    }
  }

  console.log(`Updated ${updatedCount} prices to lowercase.`);
  console.log(`Deleted ${deletedCount} duplicate uppercase prices.`);
  process.exit(0);
}
forceLower();
