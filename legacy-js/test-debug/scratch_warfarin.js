require('dotenv').config({ path: require('path').resolve(__dirname, '.env') });
const mongoose = require('mongoose');

async function main() {
  await mongoose.connect(process.env.MONGO_URL, { dbName: 'MEDSAVE' });
  const M = mongoose.model('Med', new mongoose.Schema({}, { strict: false }), 'medicines');

  // 1. What does the Warfarin record look like?
  console.log("=== Warfarin medicines in DB ===\n");
  const warfarins = await M.find(
    { $or: [
      { name: { $regex: /warfarin/i } },
      { salt: { $regex: /warfarin/i } },
      { name: { $regex: /warf/i } },
    ]},
    { name: 1, normalized_name: 1, dosage: 1, canonical_key: 1, source_platforms: 1, salt: 1, primary_salt_key: 1 }
  ).lean();

  for (const m of warfarins) {
    const hasApollo = (m.source_platforms || []).includes('apollo');
    console.log(`  ${hasApollo ? '✅' : '❌'} ${m.name} | dosage: ${m.dosage || 'null'} | salt: ${(m.salt || '').substring(0, 60)} | platforms: ${(m.source_platforms || []).join(',')}`);
  }

  // 2. What URLs does Apollo slug guess actually find for "Warfarin 5mg"?
  console.log("\n=== Testing what Apollo slug guess returns ===\n");
  const { discoverApolloUrls } = require('./etl/extract/discovery');
  const results = await discoverApolloUrls('Warfarin 5mg', { mode: 'fast', perPlatformLimit: 10, timeout: 15000 });
  console.log(`  Found ${results.length} URLs:`);
  results.forEach(r => console.log(`    ${r.url}`));

  // 3. Let's also test what the scraper extracts from these URLs
  if (results.length > 0) {
    console.log("\n=== Scraping first result to see what data we get ===\n");
    const { scrapeProductDetail } = require('./productScraper');
    try {
      const data = await scrapeProductDetail(results[0].url, { timeout: 15000, mode: 'fast' });
      console.log(`  URL: ${results[0].url}`);
      console.log(`  Name: ${data.name}`);
      console.log(`  Price: ${data.price}`);
      console.log(`  Salt: ${(data.salt || '').substring(0, 80)}`);
      console.log(`  Source type: ${data.sourceType}`);
      console.log(`  Error: ${data.error || 'none'}`);
    } catch (e) {
      console.log(`  Scrape error: ${e.message}`);
    }
  }

  // 4. Now test what Apollo search page returns for "warfarin 5mg" (via web search)
  console.log("\n=== Testing web search for Warfarin on Apollo ===\n");
  const axios = require('axios');
  const cheerio = require('cheerio');
  const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';
  
  try {
    const searchQuery = `site:apollopharmacy.in warfarin 5mg medicine`;
    const resp = await axios.get('https://html.duckduckgo.com/html/', {
      timeout: 15000,
      params: { q: searchQuery },
      headers: { 'User-Agent': UA, Accept: 'text/html' }
    });
    const $ = cheerio.load(resp.data);
    const links = [];
    $('a[href]').each((_, a) => {
      const href = $(a).attr('href');
      if (href && href.includes('apollopharmacy.in') && (href.includes('/medicine/') || href.includes('/otc/'))) {
        try {
          const parsed = new URL(href, 'https://html.duckduckgo.com');
          const uddg = parsed.searchParams.get('uddg');
          if (uddg) links.push(decodeURIComponent(uddg));
          else links.push(parsed.href);
        } catch {}
      }
    });
    console.log(`  DuckDuckGo results for "${searchQuery}":`);
    [...new Set(links)].slice(0, 10).forEach(l => console.log(`    ${l}`));
  } catch (e) {
    console.log(`  DuckDuckGo error: ${e.message}`);
  }

  // 5. Also check: what does Apollo's internal search mechanism look like?
  console.log("\n=== Testing Apollo Next.js data routes ===\n");
  const apiEndpoints = [
    'https://www.apollopharmacy.in/api/search?q=warfarin+5mg',
    'https://www.apollopharmacy.in/api/autocomplete?searchText=warfarin+5mg',
    'https://www.apollopharmacy.in/api/product/search?name=warfarin+5mg',
  ];
  
  for (const url of apiEndpoints) {
    try {
      const resp = await axios.get(url, { timeout: 5000, headers: { 'User-Agent': UA }, validateStatus: () => true });
      const contentType = resp.headers['content-type'] || '';
      console.log(`  ${url}`);
      console.log(`    Status: ${resp.status} | Type: ${contentType}`);
      if (resp.status === 200 && contentType.includes('json')) {
        console.log(`    Data: ${JSON.stringify(resp.data).substring(0, 300)}`);
      }
    } catch (e) {
      console.log(`  ${url} → ${e.message}`);
    }
  }

  await mongoose.disconnect();
}
main().catch(e => { console.error(e); process.exit(1); });
