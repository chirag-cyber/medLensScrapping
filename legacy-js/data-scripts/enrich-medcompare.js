require("dotenv").config();
const mongoose = require("mongoose");
const https = require("https");

const TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3YTk0ZjRjZC0zZDI1LTQxNjgtOTcwOS01NmRkYjJiNDlkM2MiLCJlbWFpbCI6IndvcmtpbmcyODIyQGdtYWlsLmNvbSIsImV4cCI6MTc3OTM3MTQ0NiwiaWF0IjoxNzc4NzY2NjQ2fQ.9h56D3Cc_mlWsKsVka1XgFoyLRnQtNPrUEtqsTpqFHc";

const MedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", MedicineSchema);

async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("No MONGO_URL found in .env");
    process.exit(1);
  }
  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
  console.log("Connected to MongoDB.");
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function fetchWithAuth(url, retries = 5) {
  for (let i = 0; i < retries; i++) {
    try {
      return await new Promise((resolve, reject) => {
        const req = https.get(url, {
          headers: {
            'Authorization': `Bearer ${TOKEN}`,
            'User-Agent': 'Mozilla/5.0'
          }
        }, (res) => {
          if (res.statusCode === 429) {
            return reject(new Error('429'));
          }
          if (res.statusCode !== 200) {
            return reject(new Error(`Status Code: ${res.statusCode}`));
          }
          let data = '';
          res.on('data', chunk => data += chunk);
          res.on('end', () => {
            try {
              resolve(JSON.parse(data));
            } catch (e) {
              reject(e);
            }
          });
        });
        req.on('error', reject);
      });
    } catch (e) {
      if (e.message === '429') {
        const waitTime = (i + 1) * 5000; // Wait 5s, 10s, 15s...
        console.log(`\n  -> [429 Rate Limit Hit] Pausing for ${waitTime / 1000} seconds before retrying...`);
        await sleep(waitTime);
        continue;
      }
      throw e;
    }
  }
  throw new Error('Max retries exceeded for 429 Rate Limit');
}

function isSafeMatch(dbName, fetchedName, dbSalt) {
  dbName = (dbName || "").toLowerCase();
  fetchedName = (fetchedName || "").toLowerCase();
  dbSalt = (dbSalt || "").toLowerCase();

  const noise = /\b(tablet|tablets|capsule|capsules|syrup|drop|drops|injection|cream|gel|ointment|strip|of|mg|ml|gm|mcg)\b/gi;
  const cleanDb = dbName.replace(noise, ' ').replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
  const cleanFetch = fetchedName.replace(noise, ' ').replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
  const cleanSalt = dbSalt.replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();

  const dbNums = cleanDb.match(/\d+/g) || [];
  const fetchNums = cleanFetch.match(/\d+/g) || [];
  if (dbNums.length > 0 && fetchNums.length > 0) {
    const hasCommonNum = dbNums.some(num => fetchNums.includes(num));
    if (!hasCommonNum) return false;
  }

  const dbAlpha = cleanDb.replace(/\d+/g, ' ').trim().split(/\s+/).filter(w => w.length > 1);
  const fetchAlpha = cleanFetch.replace(/\d+/g, ' ').trim().split(/\s+/).filter(w => w.length > 1);

  let nameMatched = false;
  if (dbAlpha.length > 0 && fetchAlpha.length > 0) {
    const brandWord = dbAlpha[0];
    nameMatched = fetchAlpha.some(fw => fw.includes(brandWord) || brandWord.includes(fw));
  } else {
    nameMatched = true;
  }

  if (nameMatched) return true;

  if (cleanSalt) {
    const saltWords = cleanSalt.split(/\s+/).filter(w => w.length > 3);
    if (saltWords.length > 0) {
      const saltMatched = saltWords.some(sw => fetchAlpha.some(fw => fw.includes(sw) || sw.includes(fw)));
      if (saltMatched) return true;
    }
  }
  return false;
}

//const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  await connectDB();

  const args = process.argv.slice(2);
  const limitArg = args.find(a => a.startsWith('--limit='));
  const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 0;

  // Find medicines that have a normalized_name but might be missing description or coupons
  // We'll iterate all for simplicity since $set will be safe, but prioritize those without description
  let query = { normalized_name: { $exists: true, $ne: "" }, description: { $exists: false } };

  let count = await Medicine.countDocuments(query);
  console.log(`Found ${count} medicines missing descriptions. Process starting...`);

  if (count === 0) {
    console.log("No medicines missing description. Relaxing query to enrich ALL medicines with coupons.");
    query = { normalized_name: { $exists: true, $ne: "" } };
    count = await Medicine.countDocuments(query);
  }

  const cursor = Medicine.find(query).cursor();
  let processed = 0;
  let updated = 0;

  for await (const doc of cursor) {
    if (limit > 0 && processed >= limit) break;
    processed++;

    const name = doc.get("normalized_name");
    if (!name) continue;

    console.log(`[${processed}/${limit || count}] Fetching data for: ${name}`);

    let updateFields = {};
    let isModified = false;

    // 1. Fetch Medicine Info (Try Brand Name first, Fallback to Salt)
    try {
      let infoUrl = `https://www.medcompare.in/api/medicine-info?q=${encodeURIComponent(name)}`;
      let info;
      let usedFallback = false;

      try {
        info = await fetchWithAuth(infoUrl);
      } catch (e) {
        if (e.message.includes('404')) {
          const dbSalt = doc.get("salt");
          const primarySalt = doc.get("primary_salt_key");
          const fallbackQuery = primarySalt || (dbSalt ? dbSalt.split(' ')[0] : null);
          
          if (fallbackQuery && fallbackQuery.trim() !== '') {
            console.log(`  -> [404] Brand name not found. Trying fallback salt: "${fallbackQuery}"`);
            infoUrl = `https://www.medcompare.in/api/medicine-info?q=${encodeURIComponent(fallbackQuery)}`;
            info = await fetchWithAuth(infoUrl);
            usedFallback = true;
          } else {
            throw e; // No valid fallback, throw the 404
          }
        } else {
          throw e; // Throw other errors (like 429, 500)
        }
      }

      const fetchedName = info.name || info.brand_name || info.generic_name || "";
      const dbName = doc.get("name") || name;
      const dbSalt = doc.get("salt") || "";

      // If we used the salt fallback, we know it's a generic match, so we can be a bit more lenient,
      // but let's still run it through our safety check just in case it's completely wrong.
      if (!fetchedName || (!usedFallback && !isSafeMatch(dbName, fetchedName, dbSalt))) {
          console.log(`  -> [Safety Reject] DB Name: "${dbName}" !== Fetched: "${fetchedName}"`);
      } else {
          // Map MedCompare fields to our DB if they don't already exist in the doc
          const mapField = (dbField, mcField) => {
            if (info[mcField] && typeof info[mcField] === 'string' && info[mcField].trim() !== '') {
              const current = doc.get(dbField);
              if (!current || (typeof current === 'string' && current.trim() === '')) {
                updateFields[dbField] = info[mcField].trim();
                isModified = true;
              }
            }
          };

          mapField("description", "description");
          mapField("product_intro", "product_intro");
          mapField("uses", "uses");
          mapField("side_effects", "side_effects");
          mapField("how_to_use", "how_to_use");
          mapField("how_it_works", "how_it_works");
          mapField("safety_advice", "safety_advice");
      }
    } catch (e) {
      if (e.message.includes('404')) {
        console.log(`  -> Failed info fetch: Status Code: 404 (No clinical info found)`);
      } else {
        console.log(`  -> Failed info fetch: ${e.message}`);
      }
    }

    // 2. Fetch Multi-Search for Coupons
    try {
      const msUrl = `https://www.medcompare.in/api/multi-search?ids=${encodeURIComponent(name)}`;
      const msData = await fetchWithAuth(msUrl);

      if (msData && msData.medicines && msData.medicines.length > 0) {
        const fetchedMed = msData.medicines[0];
        const fetchedName = fetchedMed.name || fetchedMed.brand_name || "";
        const dbName = doc.get("name") || name;
        const dbSalt = doc.get("salt") || "";

        if (!fetchedName || !isSafeMatch(dbName, fetchedName, dbSalt)) {
          console.log(`  -> [Safety Reject Coupons] DB Name: "${dbName}" !== Fetched: "${fetchedName}"`);
        } else {
          const coupons = fetchedMed.coupons;
          if (coupons && Object.keys(coupons).length > 0) {
            updateFields["coupons"] = coupons;
            isModified = true;
          }
        }
      }
    } catch (e) {
      console.log(`  -> Failed coupon fetch: ${e.message}`);
    }

    // Apply safe update
    if (isModified) {
      try {
        await Medicine.updateOne({ _id: doc._id }, { $set: updateFields });
        console.log(`  -> Updated fields: ${Object.keys(updateFields).join(', ')}`);
        updated++;
      } catch (e) {
        console.log(`  -> Error updating DB: ${e.message}`);
      }
    } else {
      console.log(`  -> No new data to add.`);
    }

    // Slower polite delay (2.5 seconds) to avoid triggering the 429 Rate Limit 
    await sleep(2500);
  }

  console.log(`\nProcess Complete. Processed ${processed}, Updated ${updated} records.`);
  process.exit(0);
}

main().catch(err => {
  console.error("Fatal Error:", err);
  process.exit(1);
});
