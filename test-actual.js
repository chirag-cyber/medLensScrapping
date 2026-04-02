require('dotenv').config();
const axios = require('axios');
const mongoose = require('mongoose');
const { runETL } = require('./etl/pipeline');
const { searchMedicines } = require('./etl/search');

async function scrapeRealData(query) {
  console.log(`---- 1. SCRAPING ACTUAL ${query.toUpperCase()} DATA ----`);
  
  const results = [];
  
  try {
    // 1mg Request
    const url1mg = `https://www.1mg.com/search/all?name=${encodeURIComponent(query)}`;
    console.log(`Hitting local server to scrape: ${url1mg}`);
    const res1mg = await axios.get(`http://localhost:8000/scrape-details?url=${encodeURIComponent(url1mg)}`);
    
    if (res1mg.data && res1mg.data.products) {
      res1mg.data.products.forEach(p => {
        results.push({
          name: p.name,
          price: p.price,
          url: p.url,
          platform: "1mg", // Infer platform
          salt: query // We mock salt extraction since the web-scraper doesn't grab it via DOM yet
        });
      });
    }

    // PharmEasy Request 
    const urlPharmEasy = `https://pharmeasy.in/search/all?name=${encodeURIComponent(query)}`;
    console.log(`Hitting local server to scrape: ${urlPharmEasy}`);
    const resPharmEasy = await axios.get(`http://localhost:8000/scrape-details?url=${encodeURIComponent(urlPharmEasy)}`);
    
    if (resPharmEasy.data && resPharmEasy.data.products) {
      resPharmEasy.data.products.forEach(p => {
        results.push({
          name: p.name,
          price: p.price,
          url: p.url,
          platform: "pharmeasy",
          salt: query
        });
      });
    }

    console.log(`\nCollected ${results.length} authentic ${query} products.`);
    
    return results;

  } catch (err) {
    console.error("Failed to fetch real data:", err.message);
    process.exit(1);
  }
}

async function main() {
  const query = process.argv[2];
  if (!query) {
    console.error("Please provide a medicine or salt name as an argument. Example: node test-actual.js 'vitamin c'");
    process.exit(1);
  }

  const rawData = await scrapeRealData(query);

  console.log("\n---- 2. RUNNING ETL PIPELINE ON ACTUAL DATA (SAVING TO MONGODB) ----");
  await runETL(rawData);

  console.log("\n✅ Data Successfully saved to MongoDB hidden in the background.");

  setTimeout(() => {
    mongoose.disconnect();
    process.exit(0);
  }, 1000);
}

main();
