const express = require("express");
const cors = require("cors");
const { scrapePDPLinks } = require("./scraper");
const { scrapeProductDetails, scrapeProductDetail } = require("./productScraper");
const { saveScrapeResult, getSavedScrapes, getSavedScrapeById, getSavedScrapeByUrl } = require("./db");

const app = express();
const PORT = process.env.PORT || 8000;

app.use(cors());
app.use(express.json());

// ─── Health check ────────────────────────────────────────────────────
app.get("/", (_req, res) => {
  res.json({
    service: "PDP Link Scraper + Product Detail Extractor",
    version: "2.0.0",
    endpoints: {
      scrape: {
        method: "GET",
        path: "/scrape?url=<encoded_url>&fetchDetails=true",
        description: "Scrape PDP links. Add fetchDetails=true to also extract product details.",
      },
      scrapeDetails: {
        method: "GET",
        path: "/scrape-details?url=<encoded_url>&limit=10&concurrency=5",
        description: "Scrape PDP links AND extract full product details (name, price, MRP, quantity, etc.)",
      },
      productDetail: {
        method: "GET",
        path: "/product-detail?url=<encoded_pdp_url>",
        description: "Extract product details from a single PDP URL.",
      },
      savedData: {
        method: "GET",
        path: "/saved-data?includeJson=false",
        description: "Fetch previously saved scrape results from the local database.",
      },
    },
  });
});

// ─── Scrape endpoint ─────────────────────────────────────────────────
app.get("/scrape", async (req, res) => {
  const { url, sameDomain, timeout, mode } = req.query;

  if (!url) {
    return res.status(400).json({
      success: false,
      error: "Missing required query parameter: url",
      usage: "GET /scrape?url=<encoded_url>&mode=auto|fast|browser",
    });
  }

  // Validate URL format
  try {
    new URL(url);
  } catch {
    return res.status(400).json({
      success: false,
      error: `Invalid URL format: ${url}`,
    });
  }

  try {
    // Check Cache
    const cachedScrape = await getSavedScrapeByUrl(url);
    if (cachedScrape && cachedScrape.result_json) {
      const needsDetails = req.query.fetchDetails === "true";
      const hasDetails = cachedScrape.products_scraped > 0;
      
      // If user doesn't need details, or if it already has details
      if (!needsDetails || hasDetails) {
        console.log(`[CACHE HIT] Returning saved data for: ${url}`);
        return res.json(cachedScrape.result_json);
      }
    }
    
    const options = {
      sameDomain: sameDomain !== "false",
      timeout: timeout ? parseInt(timeout, 10) : 15000,
      mode: mode || "auto",   // auto | fast | browser
    };

    console.log(`[SCRAPE] Fetching PDP links from: ${url} (mode: ${options.mode})`);
    const result = await scrapePDPLinks(url, options);
    console.log(`[SCRAPE] Found ${result.pdpLinksFound} PDP links (via ${result.mode})`);

    // Optionally fetch product details for each PDP link
    if (req.query.fetchDetails === "true" && result.pdpLinks.length > 0) {
      const limit = parseInt(req.query.limit, 10) || 10;
      const concurrency = parseInt(req.query.concurrency, 10) || 5;
      const pdpUrls = result.pdpLinks.slice(0, limit).map((l) => l.url);

      console.log(`[SCRAPE] Fetching details for ${pdpUrls.length} products...`);
      const products = await scrapeProductDetails(pdpUrls, {
        concurrency,
        timeout: options.timeout,
        mode: options.mode,
      });
      result.products = products;
      result.productsScraped = products.length;
    }

    // Save to database
    await saveScrapeResult(result);

    return res.json(result);
  } catch (err) {
    console.error(`[SCRAPE ERROR] ${err.message}`);
    return res.status(500).json({
      success: false,
      error: err.message,
      source: url,
    });
  }
});

// ─── Scrape Details endpoint ─────────────────────────────────────────
app.get("/scrape-details", async (req, res) => {
  const { url, limit, concurrency, timeout, mode, sameDomain } = req.query;

  if (!url) {
    return res.status(400).json({
      success: false,
      error: "Missing required query parameter: url",
      usage: "GET /scrape-details?url=<encoded_url>&limit=10&concurrency=5",
    });
  }

  try {
    new URL(url);
  } catch {
    return res.status(400).json({
      success: false,
      error: `Invalid URL format: ${url}`,
    });
  }

  try {
    // Check cache
    const cachedScrape = await getSavedScrapeByUrl(url);
    if (cachedScrape && cachedScrape.result_json && cachedScrape.products_scraped > 0) {
      console.log(`[CACHE HIT] Returning saved detailed data for: ${url}`);
      return res.json(cachedScrape.result_json);
    }

    const scrapeOpts = {
      sameDomain: sameDomain !== "false",
      timeout: timeout ? parseInt(timeout, 10) : 15000,
      mode: mode || "auto",
    };

    // Step 1: Get PDP links
    console.log(`[SCRAPE-DETAILS] Step 1: Finding PDP links from: ${url}`);
    const linkResult = await scrapePDPLinks(url, scrapeOpts);
    console.log(`[SCRAPE-DETAILS] Found ${linkResult.pdpLinksFound} PDP links`);

    if (linkResult.pdpLinks.length === 0) {
      return res.json({
        success: true,
        source: url,
        scrapedAt: new Date().toISOString(),
        pdpLinksFound: 0,
        productsScraped: 0,
        products: [],
        message: "No PDP links found on this page.",
      });
    }

    // Step 2: Scrape details from each PDP
    const poolSize = parseInt(req.query.poolSize, 10) || 100;
    const validLimit = parseInt(req.query.validLimit, 10) || parseInt(limit, 10) || 15;
    const maxConcurrency = parseInt(concurrency, 10) || 5;

    // We take a large pool of URLs (up to poolSize) but we only return up to `validLimit` completely valid products
    const pdpUrlsPool = linkResult.pdpLinks.slice(0, poolSize).map((l) => l.url);

    console.log(`[SCRAPE-DETAILS] Step 2: Scanning up to ${pdpUrlsPool.length} links to find ${validLimit} fully detailed products...`);

    const validProducts = [];
    const allProductsAttempted = [];

    // Process in batches of concurrency to avoid pulling all 100 at once if we only need 15
    for (let i = 0; i < pdpUrlsPool.length; i += maxConcurrency) {
      if (validProducts.length >= validLimit) break; // Reached goal

      const chunkUrls = pdpUrlsPool.slice(i, i + maxConcurrency);
      console.log(`  📦 Batch: Scraping ${chunkUrls.length} product(s)...`);

      const chunkProducts = await scrapeProductDetails(chunkUrls, {
        concurrency: maxConcurrency,
        timeout: scrapeOpts.timeout,
        mode: scrapeOpts.mode,
      });

      allProductsAttempted.push(...chunkProducts);

      // Check which products are "fully valid" (have name and price!)
      const validInChunk = chunkProducts.filter((p) => !p.error && p.name && p.price !== null);
      
      validProducts.push(...validInChunk);
      console.log(`     -> Found ${validInChunk.length} valid products in this batch (Total: ${validProducts.length}/${validLimit})`);
    }

    // Trim to exact required length in case the last batch pushed us over the limit
    const finalProductsToReturn = validProducts.slice(0, validLimit);

    console.log(`[SCRAPE-DETAILS] Done! Returned ${finalProductsToReturn.length} fully detailed products out of ${allProductsAttempted.length} attempted.`);

    const finalResult = {
      success: true,
      source: url,
      mode: linkResult.mode,
      scrapedAt: new Date().toISOString(),
      pdpLinksFound: linkResult.pdpLinksFound,
      productsScraped: allProductsAttempted.length,
      productsSuccessful: finalProductsToReturn.length,
      products: finalProductsToReturn,
    };

    // Save to database
    await saveScrapeResult(finalResult);

    return res.json(finalResult);
  } catch (err) {
    console.error(`[SCRAPE-DETAILS ERROR] ${err.message}`);
    return res.status(500).json({
      success: false,
      error: err.message,
      source: url,
    });
  }
});

// ─── Single Product Detail endpoint ─────────────────────────────────
app.get("/product-detail", async (req, res) => {
  const { url, timeout, mode } = req.query;

  if (!url) {
    return res.status(400).json({
      success: false,
      error: "Missing required query parameter: url",
      usage: "GET /product-detail?url=<encoded_pdp_url>",
    });
  }

  try {
    new URL(url);
  } catch {
    return res.status(400).json({
      success: false,
      error: `Invalid URL format: ${url}`,
    });
  }

  try {
    console.log(`[PRODUCT-DETAIL] Scraping: ${url}`);
    const product = await scrapeProductDetail(url, {
      timeout: timeout ? parseInt(timeout, 10) : 15000,
      mode: mode || "auto",
    });

    return res.json({
      success: !product.error,
      scrapedAt: new Date().toISOString(),
      product,
    });
  } catch (err) {
    console.error(`[PRODUCT-DETAIL ERROR] ${err.message}`);
    return res.status(500).json({
      success: false,
      error: err.message,
      source: url,
    });
  }
});

// ─── DB Endpoints ───────────────────────────────────────────────────
app.get("/saved-data", async (req, res) => {
  const includeJson = req.query.includeJson === "true";
  const limit = parseInt(req.query.limit, 10) || 50;

  try {
    const scrapes = await getSavedScrapes(includeJson, limit);
    return res.json({
      success: true,
      count: scrapes.length,
      data: scrapes,
    });
  } catch (err) {
    return res.status(500).json({ success: false, error: "Failed to read database" });
  }
});

app.get("/saved-data/:id", async (req, res) => {
  try {
    const scrape = await getSavedScrapeById(req.params.id);
    if (!scrape) {
      return res.status(404).json({ success: false, error: "Scrape not found" });
    }
    return res.json({ success: true, data: scrape });
  } catch (err) {
    return res.status(500).json({ success: false, error: "Failed to read database" });
  }
});

// ─── Start server ────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`
╔════════════════════════════════════════════════════════════════╗
║        PDP Scraper + Product Details — Ready 🚀              ║
╠════════════════════════════════════════════════════════════════╣
║  Server          : http://localhost:${PORT}                      ║
║  PDP Links       : GET /scrape?url=<url>                      ║
║  Links+Details   : GET /scrape-details?url=<url>&limit=10     ║
║  Single Product  : GET /product-detail?url=<pdp_url>          ║
║  View DB Data    : GET /saved-data                            ║
╚════════════════════════════════════════════════════════════════╝
  `);
});

module.exports = app;
