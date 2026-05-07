const mongoose = require("mongoose");
require("dotenv").config();
async function check() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: "MEDSAVE" });
  const M = mongoose.model("Medicine", new mongoose.Schema({}, { strict: false }));
  const m = await M.findOne({ name: /dolo-650/i }, {
    name: 1, normalized_name: 1, dosage: 1, salt: 1, normalized_salt: 1, 
    source_platforms: 1, primary_salt_key: 1
  }).lean();
  console.log(JSON.stringify(m, null, 2));
  
  // Also check azikem
  const a = await M.findOne({ name: /azikem 500/i }, {
    name: 1, normalized_name: 1, dosage: 1, salt: 1, normalized_salt: 1,
    source_platforms: 1, primary_salt_key: 1
  }).lean();
  console.log("\n--- Azikem ---");
  console.log(JSON.stringify(a, null, 2));
  process.exit(0);
}
check();
