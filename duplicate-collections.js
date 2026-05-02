require('dotenv').config();
const mongoose = require('mongoose');

async function duplicateCollections() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const db = mongoose.connection.db;

  console.log("Backing up 'medicines' collection to 'medicines_backup'...");
  await db.collection('medicines').aggregate([{ $match: {} }, { $out: 'medicines_backup' }]).toArray();
  
  console.log("Backing up 'prices' collection to 'prices_backup'...");
  await db.collection('prices').aggregate([{ $match: {} }, { $out: 'prices_backup' }]).toArray();
  
  console.log("Collections successfully duplicated!");
  mongoose.disconnect();
}

duplicateCollections().catch(console.error);
