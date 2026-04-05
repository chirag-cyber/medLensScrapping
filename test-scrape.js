require("dotenv").config();
const mongoose = require("mongoose");
const { scrapeProductDetail } = require("./productScraper");

async function main() {
  const url = process.argv[2];
  if (!url) {
    console.error("Please provide a URL as an argument");
    process.exit(1);
  }

  try {
    console.log(`Scraping ${url}...`);
    const result = await scrapeProductDetail(url, {
      timeout: 15000,
      mode: "auto"
    });

    console.log("Raw scraped data:");
    console.log(JSON.stringify(result.merged, null, 2));
  } catch (error) {
    console.error(`Error: ${error.message}`);
  } finally {
    setTimeout(() => {
      mongoose.disconnect();
      process.exit(0);
    }, 1000);
  }
}

main();