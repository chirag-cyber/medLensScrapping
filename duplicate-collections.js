require('dotenv').config();
const mongoose = require('mongoose');

async function duplicateCollections() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const db = mongoose.connection.db;

  await db.collection('medicines').aggregate([{ $match: {} }, { $out: 'medicines_backup' }]).toArray();
  
  await db.collection('prices').aggregate([{ $match: {} }, { $out: 'prices_backup' }]).toArray();
  
  mongoose.disconnect();
}

duplicateCollections().catch(() => {});
