const express = require("express");
const cors = require("cors");
const { scrapePDPLinks } = require("./scraper");
const { scrapeProductDetails, scrapeProductDetail } = require("./productScraper");
const { runETL } = require("./etl/pipeline");
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

    // Run the extracted products through the internal MongoDB ETL Pipeline silently
    const etlPayload = finalProductsToReturn.map((p) => {
      let platform = "unknown";
      if (p.url.includes("pharmeasy.in")) platform = "pharmeasy";
      else if (p.url.includes("1mg.com")) platform = "1mg";
      else if (p.url.includes("netmeds.com")) platform = "netmeds";
      else if (p.url.includes("apollopharmacy")) platform = "apollo";
      
      return {
        name: p.name,
        price: p.price,
        url: p.url,
        platform: platform,
        salt: null // Standard Scraper DOM doesn't get salt natively yet
      };
    });
    // Fire and forget the pipeline
    runETL(etlPayload).catch(e => console.error("[ETL BACKGROUND ERROR]", e));


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

// Database routes removed in favor of direct ETL pipelining.

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
╚════════════════════════════════════════════════════════════════╝
  `);
});

module.exports = app;
