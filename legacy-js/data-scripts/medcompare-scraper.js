require("dotenv").config();
const mongoose = require("mongoose");
const https = require("https");

const TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3YTk0ZjRjZC0zZDI1LTQxNjgtOTcwOS01NmRkYjJiNDlkM2MiLCJlbWFpbCI6IndvcmtpbmcyODIyQGdtYWlsLmNvbSIsImV4cCI6MTc3OTM3MTQ0NiwiaWF0IjoxNzc4NzY2NjQ2fQ.9h56D3Cc_mlWsKsVka1XgFoyLRnQtNPrUEtqsTpqFHc";

const MedicineSchema = new mongoose.Schema({}, { strict: false, timestamps: true });
const PriceSchema = new mongoose.Schema({}, { strict: false, timestamps: true });

const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", MedicineSchema);
const Price = mongoose.models.Price || mongoose.model("Price", PriceSchema);

async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("No MONGO_URL found in .env");
    process.exit(1);
  }
  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
  console.log("Connected to MongoDB.");
}

function fetchMedCompare(query) {
    return new Promise((resolve, reject) => {
        const encodedQuery = encodeURIComponent(query);
        const url = `https://www.medcompare.in/api/direct-search?q=${encodedQuery}&token=${TOKEN}`;
        
        const results = [];
        const req = https.get(url, {
            headers: {
                'Accept': 'text/event-stream',
                'User-Agent': 'Mozilla/5.0'
            }
        }, (res) => {
            if (res.statusCode !== 200) {
                return reject(new Error(`Status Code: ${res.statusCode}`));
            }

            res.on('data', (chunk) => {
                const lines = chunk.toString().split('\n');
                for (let line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.substring(6).trim());
                            if (data.event !== 'complete') {
                                results.push(data);
                            }
                        } catch (e) {
                            // ignore json parse error on incomplete chunks or heartbeat
                        }
                    }
                }
            });

            res.on('end', () => {
                resolve(results);
            });
        });

        req.on('error', reject);
    });
}

function mapStoreToPlatform(storeName) {
    const s = storeName.toLowerCase();
    if (s.includes('apollo')) return 'apollo';
    if (s.includes('netmeds')) return 'netmeds';
    if (s.includes('pharmeasy')) return 'pharmeasy';
    if (s.includes('1mg') || s.includes('tata')) return '1mg';
    if (s.includes('medplus')) return 'medplus';
    return storeName.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Safety check to ensure the fetched medicine name matches the target DB medicine name.
 * We require the first alphabetic word (brand name) to match and any dosages (numbers) to overlap.
 * If the brand name does not match, but the dosage AND salt match, it is allowed as a valid generic.
 */
function isSafeMatch(dbName, fetchedName, dbSalt) {
    dbName = dbName.toLowerCase();
    fetchedName = fetchedName.toLowerCase();
    dbSalt = (dbSalt || "").toLowerCase();

    // Remove noise words and units
    const noise = /\b(tablet|tablets|capsule|capsules|syrup|drop|drops|injection|cream|gel|ointment|strip|of|mg|ml|gm|mcg)\b/gi;
    const cleanDb = dbName.replace(noise, ' ').replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
    const cleanFetch = fetchedName.replace(noise, ' ').replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();
    const cleanSalt = dbSalt.replace(/[^a-z0-9]/g, ' ').replace(/\s+/g, ' ').trim();

    // Ensure numbers match strictly if both have numbers
    const dbNums = cleanDb.match(/\d+/g) || [];
    const fetchNums = cleanFetch.match(/\d+/g) || [];
    if (dbNums.length > 0 && fetchNums.length > 0) {
        const hasCommonNum = dbNums.some(num => fetchNums.includes(num));
        if (!hasCommonNum) return false; // If numbers exist but don't match, reject immediately
    }

    // Extract alphabetic words
    const dbAlpha = cleanDb.replace(/\d+/g, ' ').trim().split(/\s+/).filter(w => w.length > 1);
    const fetchAlpha = cleanFetch.replace(/\d+/g, ' ').trim().split(/\s+/).filter(w => w.length > 1);

    // The primary brand name is usually the first word
    let nameMatched = false;
    if (dbAlpha.length > 0 && fetchAlpha.length > 0) {
        const brandWord = dbAlpha[0];
        nameMatched = fetchAlpha.some(fw => fw.includes(brandWord) || brandWord.includes(fw));
    } else {
        nameMatched = true;
    }

    if (nameMatched) return true;

    // If name doesn't match, check if salt matches (and we already verified nums match above)
    if (cleanSalt) {
        const saltWords = cleanSalt.split(/\s+/).filter(w => w.length > 3);
        if (saltWords.length > 0) {
            const saltMatched = saltWords.some(sw => fetchAlpha.some(fw => fw.includes(sw) || sw.includes(fw)));
            if (saltMatched) {
                return true; // Name didn't match, but Salt + Dosage matched!
            }
        }
    }

    return false;
}

async function scrapePrices(limit = 0) {
    await connectDB();
    
    // Fetch medicines that haven't been scraped yet
    let query = Medicine.find({ medcompare_scraped: { $ne: true } }).lean();
    if (limit > 0) {
        query = query.limit(limit);
    }
    const medicines = await query;
    console.log(`Found ${medicines.length} medicines to process...`);
    
    for (const med of medicines) {
        console.log(`\nFetching prices for: ${med.name}`);
        try {
            const results = await fetchMedCompare(med.name);
            console.log(`Found ${results.length} price entries from MedCompare.`);
            
            for (const item of results) {
                if (!item.store || !item.price) continue;
                
                // --- SAFETY CHECK ---
                if (!isSafeMatch(med.name, item.name, med.salt)) {
                    console.log(`  -> Skipped [Safety Mismatch]: DB="${med.name}" vs Fetched="${item.name}"`);
                    continue;
                }
                
                const platform = mapStoreToPlatform(item.store);
                
                const priceDoc = {
                    medicine_id: med._id,
                    platform: platform,
                    price: parseFloat(item.price),
                    mrp: parseFloat(item.mrp || item.price),
                    discount_percent: parseFloat(item.discount || 0),
                    url: item.url,
                    in_stock: item.in_stock,
                    name_on_platform: item.name,
                    updatedAt: new Date()
                };

                // Upsert the price
                await Price.findOneAndUpdate(
                    { medicine_id: med._id, platform: platform },
                    { $set: priceDoc },
                    { upsert: true, returnDocument: 'after' }
                );
                
                console.log(`  -> Upserted ${platform} price: ₹${priceDoc.price} (MRP: ₹${priceDoc.mrp})`);
            }
            
            // Also update the source_platforms array on the medicine and mark as scraped
            const platformsFound = results.map(r => mapStoreToPlatform(r.store));
            const updateDoc = { $set: { medcompare_scraped: true } };
            if (platformsFound.length > 0) {
                updateDoc.$addToSet = { source_platforms: { $each: platformsFound } };
            }
            await Medicine.updateOne({ _id: med._id }, updateDoc);
            
        } catch (error) {
            console.error(`Failed to fetch for ${med.name}:`, error.message);
        }
        
        // Wait a bit to avoid rate limiting
        await new Promise(resolve => setTimeout(resolve, 1000));
    }
    
    console.log("\nDone processing.");
    process.exit(0);
}

// Get limit from CLI args, default to all if not provided. e.g. node medcompare-scraper.js --limit 10
const limitArg = process.argv.find(arg => arg.startsWith('--limit='));
const limit = limitArg ? parseInt(limitArg.split('=')[1]) : 0; // 0 means all

scrapePrices(limit);

