require('dotenv').config();
const mongoose = require('mongoose');

function normalizeDosage(dosage) {
  if (!dosage) return null;
  let d = String(dosage).toLowerCase().replace(/\s+/g, "").trim();
  d = d.replace(/\.0(?=mg|ml|mcg|g|kg|%)/i, "");
  return d || null;
}

function stripManufacturer(name, manufacturer) {
  if (!name) return "";
  let stripped = name.toLowerCase();
  if (manufacturer) {
    const mTokens = manufacturer.toLowerCase().split(/\s+/).filter(w => w.length > 2 && !["ltd", "pvt", "limited", "private", "pharma", "pharmaceuticals"].includes(w));
    for (const t of mTokens) {
      stripped = stripped.replace(new RegExp(`\\b${t}\\b`, 'g'), '');
    }
  }
  return stripped.replace(/\s+/g, ' ').trim();
}

function normalizeNameForMatching(name, manufacturer) {
  let n = stripManufacturer(name, manufacturer);
  const FORM_NOISE = ["tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "strip", "strips", "pack", "bottle", "syrup", "suspension", "injection", "drop", "drops", "ointment", "cream", "gel", "spray", "sachet", "box", "of", "new"];
  const noisePattern = new RegExp(`\\b(${FORM_NOISE.join("|")})\\b`, "gi");
  n = n.replace(noisePattern, " ");
  n = n.replace(/\bstrip\s+of\s+\d+\b/gi, " ");
  n = n.replace(/\b\d+\s*'s\b/gi, " ");
  n = n.replace(/\b(?:1|2|3|4|5|6|7|8|9|10|12|14|15|20|30|50|60|100)\s+(?:tablets?|capsules?|strips?)\b/gi, " ");
  n = n.replace(/[^a-z0-9\s\-.]/g, " ");
  
  // Remove dosage string entirely
  n = n.replace(/\b\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|gm|kg|%)\b/gi, "");
  
  n = n.replace(/\s+/g, " ").trim();
  n = n.replace(/[\s\-,.|:]+$/, "").trim();
  return n;
}

function tokenSimilarity(a, b) {
  const setA = new Set(a.split(' ').filter(Boolean));
  const setB = new Set(b.split(' ').filter(Boolean));
  if (setA.size === 0 || setB.size === 0) return 0;
  
  let intersection = 0;
  for (const word of setA) {
    if (setB.has(word)) intersection++;
  }
  
  // Overlap ratio relative to the longest string
  return intersection / Math.max(setA.size, setB.size);
}

async function analyze() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const Medicine = mongoose.model('Medicine', new mongoose.Schema({},{strict:false}));
  
  const allMeds = await Medicine.find({ is_canonical: { $ne: true } }, { name: 1, dosage: 1, manufacturer: 1, source_platforms: 1 }, { lean: true });
  
  console.log(`Loaded ${allMeds.length} medicines.`);
  
  // Group by normalized dosage
  const buckets = new Map();
  for (const med of allMeds) {
    const d = normalizeDosage(med.dosage);
    if (!d) continue;
    if (!buckets.has(d)) buckets.set(d, []);
    buckets.get(d).push(med);
  }

  let fuzzyMatchesFound = 0;
  const mergedIds = new Set();
  
  for (const [dosage, meds] of buckets) {
    if (meds.length <= 1) continue;
    
    for (let i = 0; i < meds.length; i++) {
      if (mergedIds.has(String(meds[i]._id))) continue;
      
      const matchGroup = [meds[i]];
      const nameA = normalizeNameForMatching(meds[i].name, meds[i].manufacturer);
      if (!nameA) continue;
      
      for (let j = i + 1; j < meds.length; j++) {
        if (mergedIds.has(String(meds[j]._id))) continue;
        
        const nameB = normalizeNameForMatching(meds[j].name, meds[j].manufacturer);
        if (!nameB) continue;
        
        const sim = tokenSimilarity(nameA, nameB);
        if (sim >= 0.8) {
           matchGroup.push(meds[j]);
           mergedIds.add(String(meds[j]._id));
        }
      }
      
      if (matchGroup.length > 1) {
        fuzzyMatchesFound++;
        if (fuzzyMatchesFound <= 10) {
          console.log(`\nFound Match Group for dosage [${dosage}]:`);
          matchGroup.forEach(m => console.log(` - ${m.name} [Mfr: ${m.manufacturer || 'None'}] -> Core: "${normalizeNameForMatching(m.name, m.manufacturer)}"`));
        }
      }
    }
  }
  
  console.log(`\nTotal new fuzzy groups found: ${fuzzyMatchesFound}`);
  mongoose.disconnect();
}
analyze();
