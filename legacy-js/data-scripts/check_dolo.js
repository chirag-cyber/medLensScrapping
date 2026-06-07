require('dotenv').config();
const mongoose = require('mongoose');
const M = mongoose.model('Medicine', new mongoose.Schema({}, {strict:false}));
const P = mongoose.model('Price', new mongoose.Schema({}, {strict:false}));

mongoose.connect(process.env.MONGO_URL, {dbName:'MEDSAVE'}).then(async()=>{
  const total = await M.countDocuments({normalized_name: {$exists: true, $ne: ''}});
  
  // Build sets of medicine_ids per platform
  const platforms = ['1mg', 'pharmeasy', 'apollo', 'netmeds', 'truemeds', 'medplus'];
  const coverageSets = {};
  
  for (const p of platforms) {
    const ids = await P.distinct('medicine_id', {platform: new RegExp(p, 'i'), url: {$exists: true, $ne: ''}});
    coverageSets[p] = new Set(ids.map(String));
    console.log(`${p}: ${ids.length} medicines with URLs`);
  }
  
  // How many medicines have at least one URL from any platform?
  const allMedIds = await M.distinct('_id', {normalized_name: {$exists: true, $ne: ''}});
  let coveredByAny = 0;
  let coveredBy1mg = 0;
  let notCoveredBy1mg = [];
  
  for (const id of allMedIds) {
    const sid = String(id);
    const has1mg = coverageSets['1mg'].has(sid);
    const hasAny = platforms.some(p => coverageSets[p].has(sid));
    if (hasAny) coveredByAny++;
    if (has1mg) coveredBy1mg++;
    if (!has1mg && hasAny) {
      // Find which platform covers this
      const coveredBy = platforms.filter(p => coverageSets[p].has(sid));
      notCoveredBy1mg.push({id: sid, platforms: coveredBy});
    }
  }
  
  console.log(`\n--- Coverage Summary ---`);
  console.log(`Total medicines: ${total}`);
  console.log(`Covered by 1mg: ${coveredBy1mg} (${Math.round(coveredBy1mg/total*100)}%)`);
  console.log(`Covered by ANY platform: ${coveredByAny} (${Math.round(coveredByAny/total*100)}%)`);
  console.log(`NOT covered by 1mg but covered by others: ${notCoveredBy1mg.length}`);
  console.log(`NOT covered by ANY platform: ${total - coveredByAny}`);
  
  // Breakdown of fallback platforms for those missing 1mg
  const fallbackCount = {};
  for (const item of notCoveredBy1mg) {
    for (const p of item.platforms) {
      fallbackCount[p] = (fallbackCount[p] || 0) + 1;
    }
  }
  console.log(`\n--- Fallback coverage (medicines without 1mg URL) ---`);
  for (const [p, c] of Object.entries(fallbackCount).sort((a,b) => b[1]-a[1])) {
    console.log(`  ${p}: ${c} medicines`);
  }
  
  process.exit(0);
});
