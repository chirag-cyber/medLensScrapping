#!/usr/bin/env node

/**
 * 🔍 discover-alternatives.js
 *
 * A specialized script that searches platforms by SALT COMPOSITION
 * rather than brand name. This discovers alternative brands for the same
 * salt (e.g., searching "Metformin 1000mg" to find "Maxformin 1000").
 * 
 * Found URLs are passed to the standard ETL pipeline to be ingested as
 * NEW medicine records, allowing the frontend to group them as alternatives.
 *
 * Usage:
 *   node discover-alternatives.js --query "warfarin 5mg"
 *   node discover-alternatives.js --limit 10   # run for top N salts in DB
 */

require("dotenv").config({ path: require("path").resolve(__dirname, ".env") });
const mongoose = require("mongoose");
const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

const { scrapeProductDetail } = require("./productScraper");
const { runETL } = require("./etl/pipeline");
const { discoverPharmEasyUrls, discoverNetmedsUrls, discover1mgUrls } = require("./etl/extract/discovery");

const ARGV = process.argv.slice(2);
const QUERY_INDEX = ARGV.indexOf("--query");
const LIMIT_INDEX = ARGV.indexOf("--limit");
const TARGET_QUERY = QUERY_INDEX >= 0 ? ARGV[QUERY_INDEX + 1] : null;
const LIMIT = LIMIT_INDEX >= 0 ? parseInt(ARGV[LIMIT_INDEX + 1], 10) : 5;

async function connectDB() {
  const MONGO_URI = process.env.MONGO_URL;
  if (!MONGO_URI) {
    console.error("Missing MONGO_URL in .env");
    process.exit(1);
  }
  await mongoose.connect(MONGO_URI, { dbName: "MEDSAVE" });
  console.log("[Alternatives] Connected to database.");
}

/**
 * Apollo search results are rendered client-side (SPA).
 * We MUST use Puppeteer to execute a search and extract URLs.
 */
async function getApolloAlternatives(query) {
  console.log(`[Alternatives] Searching Apollo via Puppeteer for: "${query}"`);
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const searchUrl = `https://www.apollopharmacy.in/search-medicines/${encodeURIComponent(query)}`;
  
  try {
    await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
    try {
      await page.waitForSelector('a[href*="/medicine/"], a[href*="/otc/"]', { timeout: 10000 });
    } catch (e) {
      // Timeout might just mean no results
    }

    const links = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('a'))
        .map(a => a.href)
        .filter(href => href && (href.includes('/medicine/') || href.includes('/otc/')) && href.includes('apollopharmacy.in'));
    });
    
    await browser.close();
    
    // Deduplicate and limit to top 5 to prevent DB explosion
    const uniqueLinks = [...new Set(links)].slice(0, 5);
    console.log(`[Alternatives] Found ${uniqueLinks.length} results on Apollo.`);
    return uniqueLinks.map(url => ({
      url,
      platform: "apollo",
      discoveryMethod: "puppeteer-search",
      sourceUrl: searchUrl
    }));
  } catch (err) {
    await browser.close();
    console.error(`[Alternatives] Error searching Apollo: ${err.message}`);
    return [];
  }
}

/**
 * Helper to discover across targeted platforms for a salt query
 */
async function discoverPlatforms(query, targetPlatforms = ["apollo", "pharmeasy", "netmeds", "1mg"]) {
  const allUrls = [];
  
  // 1. Apollo (Puppeteer)
  if (targetPlatforms.includes("apollo")) {
    const apolloUrls = await getApolloAlternatives(query);
    allUrls.push(...apolloUrls);
  }
  
  // 2. PharmEasy (API)
  if (targetPlatforms.includes("pharmeasy")) {
    try {
      console.log(`[Alternatives] Searching PharmEasy for: "${query}"`);
      const peUrls = await discoverPharmEasyUrls(query, { perPlatformLimit: 5 });
      console.log(`[Alternatives] Found ${peUrls.length} results on PharmEasy.`);
      allUrls.push(...peUrls);
    } catch (e) { console.error(e.message); }
  }
  
  // 3. Netmeds (HTML/API)
  if (targetPlatforms.includes("netmeds")) {
    try {
      console.log(`[Alternatives] Searching Netmeds for: "${query}"`);
      const nmUrls = await discoverNetmedsUrls(query, { perPlatformLimit: 5 });
      console.log(`[Alternatives] Found ${nmUrls.length} results on Netmeds.`);
      allUrls.push(...nmUrls);
    } catch (e) { console.error(e.message); }
  }
  
  // 4. 1mg (API)
  if (targetPlatforms.includes("1mg")) {
    try {
      console.log(`[Alternatives] Searching 1mg for: "${query}"`);
      const omgUrls = await discover1mgUrls(query, { perPlatformLimit: 5 });
      console.log(`[Alternatives] Found ${omgUrls.length} results on 1mg.`);
      allUrls.push(...omgUrls);
    } catch (e) { console.error(e.message); }
  }
  
  return allUrls;
}

/**
 * Process a list of discovered URLs through the standard ETL
 */
async function processDiscoveredUrls(discoveredItems) {
  let successCount = 0;
  for (const item of discoveredItems) {
    console.log(`\n  → Scraping: ${item.url}`);
    try {
      const data = await scrapeProductDetail(item.url, { mode: "fast", timeout: 20000 });
      if (data.error) {
        console.log(`    ❌ Scrape failed: ${data.error}`);
        continue;
      }
      if (data.sourceType !== "product") {
        console.log(`    ⚠️ Skipped (not a product page)`);
        continue;
      }
      
      const etlResult = await runETL([data]);
      console.log(`    ✅ ETL Result: ${etlResult.medicinesTouched} med(s) touched, ${etlResult.priceEntriesUpserted} price(s) upserted`);
      successCount += etlResult.medicinesTouched + etlResult.priceEntriesUpserted;
    } catch (err) {
      console.log(`    ❌ Pipeline error: ${err.message}`);
    }
  }
  return successCount;
}

async function main() {
  await connectDB();
  
  if (TARGET_QUERY) {
    console.log(`\n════════════════════════════════════════════════`);
    console.log(`[Alternatives] Running targeted search for: "${TARGET_QUERY}"`);
    console.log(`════════════════════════════════════════════════\n`);
    
    const urls = await discoverPlatforms(TARGET_QUERY);
    console.log(`\n[Alternatives] Total URLs found: ${urls.length}`);
    
    await processDiscoveredUrls(urls);
    
  } else {
    // Automatic mode: Find medicines missing platforms and search those platforms
    console.log(`\n[Alternatives] Auto-mode: Finding ${LIMIT} medicines missing platforms...`);
    const Medicine = mongoose.models.Medicine || mongoose.model("Medicine", new mongoose.Schema({}, { strict: false }));
    const PRIORITY_PLATFORMS = ["1mg", "pharmeasy", "netmeds", "apollo"];
    
    const medicines = await Medicine.find({
      source_platforms: { $not: { $all: PRIORITY_PLATFORMS } },
      primary_salt_key: { $ne: null },
      dosage: { $ne: null }
    }).limit(LIMIT).lean();
    
    if (medicines.length === 0) {
      console.log("[Alternatives] No medicines found missing platforms with valid salt/dosage.");
    }
    
    for (const med of medicines) {
      const sourcePlatforms = (med.source_platforms || []).map(p => p.toLowerCase());
      const missingPlatforms = PRIORITY_PLATFORMS.filter(p => !sourcePlatforms.includes(p));
      const query = `${med.primary_salt_key} ${med.dosage}`;
      
      console.log(`\n════════════════════════════════════════════════`);
      console.log(`[Alternatives] Medicine: "${med.name}"`);
      console.log(`[Alternatives] Missing: ${missingPlatforms.join(", ")}`);
      console.log(`[Alternatives] Search Query: "${query}"`);
      console.log(`════════════════════════════════════════════════\n`);
      
      const urls = await discoverPlatforms(query, missingPlatforms);
      if (urls.length > 0) {
        await processDiscoveredUrls(urls);
      } else {
        console.log(`[Alternatives] No alternative URLs found for missing platforms.`);
      }
    }
  }
  
  console.log("\n[Alternatives] Finished.");
  await mongoose.disconnect();
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
