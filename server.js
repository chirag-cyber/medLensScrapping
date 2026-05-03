const express = require("express");
const cors = require("cors");
const { scrapePDPLinks } = require("./scraper");
const { scrapeProductDetails, scrapeProductDetail } = require("./productScraper");
const { runETL } = require("./etl/pipeline");
const { ingestMedicineQuery, runBatchIngestion } = require("./etl/batchJob");
const connectDB = require("./etl/load/db");
const { searchMedicines } = require("./etl/search");
const { findIncompleteMedicines, enrichBatch } = require("./etl/enrich/enricher");
const app = express();
const PORT = process.env.PORT || 8000;

function parsePerPlatformLimit(value) {
  if (value === undefined || value === null) return null;

  const normalized = String(value).trim().toLowerCase();
  if (!normalized || normalized === "all" || normalized === "none" || normalized === "0") {
    return null;
  }

  const parsed = parseInt(normalized, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

app.use(cors());
app.use(express.json());

// ─── Health check ────────────────────────────────────────────────────
app.get("/", (_req, res) => {
  res.json({
    service: "PDP Link Scraper + Product Detail Extractor",
    version: "3.0.0",
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
      searchMedicines: {
        method: "GET",
        path: "/search-medicines?query=<salt_or_medicine_name>",
        description:
          "Return canonical medicines grouped with per-platform prices, useful for salt queries like paracetamol.",
      },
      ingestMedicine: {
        method: "GET",
        path: "/ingest-medicine?query=<medicine_name>&perPlatformLimit=all",
        description:
          "Discover medicine pages across platforms via platform search + web search, scrape them, and bulk upsert into MongoDB.",
      },
      ingestBatch: {
        method: "POST",
        path: "/ingest-batch",
        description:
          "Run the medicine discovery + ETL flow for multiple medicine queries in controlled batches.",
      },
      enrichMedicines: {
        method: "POST",
        path: "/enrich-medicines",
        description:
          "Runs the LLM enrichment pipeline to populate missing descriptions, side effects, etc. via Groq API.",
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

    const result = await scrapePDPLinks(url, options);

    // Optionally fetch product details for each PDP link
    if (req.query.fetchDetails === "true" && result.pdpLinks.length > 0) {
      const limit = parseInt(req.query.limit, 10) || 10;
      const concurrency = parseInt(req.query.concurrency, 10) || 5;
      const pdpUrls = result.pdpLinks.slice(0, limit).map((l) => l.url);

      const products = await scrapeProductDetails(pdpUrls, {
        concurrency,
        timeout: options.timeout,
        mode: options.mode,
      });
      result.products = products;
      result.productsScraped = products.length;
    }

    if (req.query.minimal === "true") {
      delete result.products;
      delete result.pdpLinks;
    }
    return res.json(result);
  } catch (err) {
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
    const linkResult = await scrapePDPLinks(url, scrapeOpts);

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

    const validProducts = [];
    const allProductsAttempted = [];

    // Process in batches of concurrency to avoid pulling all 100 at once if we only need 15
    for (let i = 0; i < pdpUrlsPool.length; i += maxConcurrency) {
      if (validProducts.length >= validLimit) break; // Reached goal

      const chunkUrls = pdpUrlsPool.slice(i, i + maxConcurrency);

      const chunkProducts = await scrapeProductDetails(chunkUrls, {
        concurrency: maxConcurrency,
        timeout: scrapeOpts.timeout,
        mode: scrapeOpts.mode,
      });

      allProductsAttempted.push(...chunkProducts);

      // Check which products are "fully valid" (have name and price!)
      const validInChunk = chunkProducts.filter((p) => !p.error && p.name && p.price !== null);
      
      validProducts.push(...validInChunk);
    }

    // Trim to exact required length in case the last batch pushed us over the limit
    const finalProductsToReturn = validProducts.slice(0, validLimit);

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
        image: p.image || null,
        salt: p.salt || null,
        description: p.description || null,
        dosage: p.dosage || null,
        sideEffects: p.sideEffects || [],
        faq: p.faq || [],
        sourceType: p.sourceType || "product",
      };
    });
    // Fire and forget the pipeline
    runETL(etlPayload).catch(() => {});

    if (req.query.minimal === "true") {
      delete finalResult.products;
    }
    return res.json(finalResult);
  } catch (err) {
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
    return res.status(500).json({
      success: false,
      error: err.message,
      source: url,
    });
  }
});

app.get("/search-medicines", async (req, res) => {
  const { query } = req.query;

  if (!query) {
    return res.status(400).json({
      success: false,
      error: "Missing required query parameter: query",
      usage: "GET /search-medicines?query=paracetamol",
    });
  }

  try {
    await connectDB();
    const results = await searchMedicines(query);

    const responseData = {
      success: true,
      searchedAt: new Date().toISOString(),
      query,
      totalMedicines: results.length,
      results,
    };
    if (req.query.minimal === "true") {
      delete responseData.results;
    }
    return res.json(responseData);
  } catch (error) {
    return res.status(500).json({
      success: false,
      error: error.message,
      query,
    });
  }
});

app.get("/ingest-medicine", async (req, res) => {
  const { query, perPlatformLimit, concurrency, timeout, mode, includeWebSearch } = req.query;
  const useWebSearch = includeWebSearch !== "false";

  if (!query) {
    return res.status(400).json({
      success: false,
      error: "Missing required query parameter: query",
      usage: "GET /ingest-medicine?query=<medicine_name>&perPlatformLimit=all",
    });
  }

  try {
    const result = await ingestMedicineQuery(query, {
      perPlatformLimit: parsePerPlatformLimit(perPlatformLimit),
      concurrency: concurrency ? parseInt(concurrency, 10) : 4,
      timeout: timeout ? parseInt(timeout, 10) : 15000,
      mode: mode || "auto",
      includeWebSearch: useWebSearch,
    });

    const responseData = {
      success: true,
      ingestedAt: new Date().toISOString(),
      result,
    };
    if (req.query.minimal === "true") {
      delete responseData.result;
    }
    return res.json(responseData);
  } catch (error) {
    return res.status(500).json({
      success: false,
      error: error.message,
      query,
    });
  }
});

app.post("/ingest-batch", async (req, res) => {
  const { queries, queryBatchSize, perPlatformLimit, concurrency, timeout, mode, includeWebSearch } =
    req.body || {};
  const useWebSearch = !(includeWebSearch === false || includeWebSearch === "false");

  const normalizedQueries = Array.isArray(queries)
    ? queries
    : typeof queries === "string"
      ? queries.split(",").map((query) => query.trim()).filter(Boolean)
      : [];

  if (normalizedQueries.length === 0) {
    return res.status(400).json({
      success: false,
      error: "Request body must include a non-empty queries array or comma-separated string.",
      usage: 'POST /ingest-batch { "queries": ["paracetamol 650", "azithromycin 500"] }',
    });
  }

  try {
    const result = await runBatchIngestion(normalizedQueries, {
      queryBatchSize: queryBatchSize ? parseInt(queryBatchSize, 10) : 2,
      perPlatformLimit: parsePerPlatformLimit(perPlatformLimit),
      concurrency: concurrency ? parseInt(concurrency, 10) : 4,
      timeout: timeout ? parseInt(timeout, 10) : 15000,
      mode: mode || "auto",
      includeWebSearch: useWebSearch,
    });

    const responseData = {
      success: true,
      ingestedAt: new Date().toISOString(),
      ...result,
    };
    if (req.body?.minimal || req.query.minimal === "true") {
      delete responseData.results;
      delete responseData.details;
    }
    return res.json(responseData);
  } catch (error) {
    return res.status(500).json({
      success: false,
      error: error.message,
    });
  }
});

// ═══════════════════════════════════════════════════════════════════════
// CRON / BATCH ROUTES — Trigger pipeline stages via HTTP for cron jobs
// ═══════════════════════════════════════════════════════════════════════
const { exec, spawn } = require("child_process");

// Track running jobs so we don't double-trigger
const runningJobs = {};

function triggerJob(jobName, command, args = []) {
  if (runningJobs[jobName]) {
    return { alreadyRunning: true, startedAt: runningJobs[jobName].startedAt };
  }

  const startedAt = new Date().toISOString();
  const child = spawn("node", [command, ...args], {
    cwd: __dirname,
    stdio: "pipe",
    detached: false,
  });

  runningJobs[jobName] = { pid: child.pid, startedAt };

  // Stream output to console instead of storing in memory (prevents OOM leak)
  child.stdout.on("data", (data) => { process.stdout.write(`[${jobName}] ${data.toString()}`); });
  child.stderr.on("data", (data) => { process.stderr.write(`[${jobName}] ${data.toString()}`); });

  child.on("close", (code) => {
    delete runningJobs[jobName];
  });

  child.on("error", (err) => {
    delete runningJobs[jobName];
  });

  return { alreadyRunning: false, startedAt, pid: child.pid };
}

// ─── 1. Scrape: Orchestrator (Discovery + Raw Scraping) ─────────────
app.get("/cron/scrape", (req, res) => {
  const mode = req.query.reset === "true" ? "--reset" : "--resume";
  const batchSize = req.query.batchSize || "3";
  const scraperMode = req.query.scraperMode || "fast"; // Default to fast for cron to avoid OOM

  const result = triggerJob("scrape", "orchestrator.js", [mode, "--batch-size", batchSize, "--scraper-mode", scraperMode]);

  if (result.alreadyRunning) {
    return res.status(409).json({
      success: false,
      error: "Scrape job is already running",
      startedAt: result.startedAt,
    });
  }

  return res.json({
    success: true,
    job: "scrape",
    message: `Orchestrator triggered in background (${mode})`,
    pid: result.pid,
    startedAt: result.startedAt,
  });
});

// ─── 2. Interlink: Deduplicate Medicines ────────────────────────────
app.get("/cron/interlink", (req, res) => {
  const dryRun = req.query.dryRun === "true" ? "--dry-run" : "";
  const args = dryRun ? [dryRun] : [];

  const result = triggerJob("interlink", "interlink-medicines.js", args);

  if (result.alreadyRunning) {
    return res.status(409).json({
      success: false,
      error: "Interlink job is already running",
      startedAt: result.startedAt,
    });
  }

  return res.json({
    success: true,
    job: "interlink",
    message: `Interlink triggered${dryRun ? " (DRY RUN)" : " (LIVE)"}`,
    pid: result.pid,
    startedAt: result.startedAt,
  });
});

// ─── 3. Targeted Enrichment: Fill missing platform prices ───────────
app.get("/cron/targeted-enrichment", (req, res) => {
  const limit = req.query.limit || "50";
  const dryRun = req.query.dryRun === "true" ? "--dry-run" : "";
  const args = ["--limit", limit];
  if (dryRun) args.push(dryRun);

  const result = triggerJob("targeted-enrichment", "targeted-enrichment.js", args);

  if (result.alreadyRunning) {
    return res.status(409).json({
      success: false,
      error: "Targeted enrichment job is already running",
      startedAt: result.startedAt,
    });
  }

  return res.json({
    success: true,
    job: "targeted-enrichment",
    message: `Targeted enrichment triggered (limit: ${limit})${dryRun ? " DRY RUN" : ""}`,
    pid: result.pid,
    startedAt: result.startedAt,
  });
});

// ─── 4. LLM Enrichment: AI-powered metadata enrichment ─────────────
app.get("/cron/llm-enrich", (req, res) => {
  const result = triggerJob("llm-enrich", "enrich-medicines.js", []);

  if (result.alreadyRunning) {
    return res.status(409).json({
      success: false,
      error: "LLM enrichment job is already running",
      startedAt: result.startedAt,
    });
  }

  return res.json({
    success: true,
    job: "llm-enrich",
    message: "LLM enrichment triggered",
    pid: result.pid,
    startedAt: result.startedAt,
  });
});

// ─── 5. Full Pipeline: Scrape → Interlink → Enrich (sequential) ────
app.get("/cron/full-pipeline", async (req, res) => {
  const startedAt = new Date().toISOString();

  if (runningJobs["full-pipeline"]) {
    return res.status(409).json({
      success: false,
      error: "Full pipeline is already running",
      startedAt: runningJobs["full-pipeline"].startedAt,
    });
  }

  runningJobs["full-pipeline"] = { startedAt };

  // Run sequentially in the background
  (async () => {
    const runScript = (script, args = []) =>
      new Promise((resolve, reject) => {
        const child = spawn("node", [script, ...args], {
          cwd: __dirname,
          stdio: "inherit", // Inherit allows the logs to flow to Render's console and prevents pipe buffer freeze
        });
        child.on("close", (code) => (code === 0 ? resolve(code) : reject(new Error(`${script} exited with code ${code}`))));
        child.on("error", reject);
      });

    try {
      await runScript("orchestrator.js", ["--resume", "--batch-size", "3"]);

      await runScript("interlink-medicines.js");

      await runScript("targeted-enrichment.js", ["--limit", "50"]);

      await runScript("enrich-medicines.js");

    } catch {
    } finally {
      delete runningJobs["full-pipeline"];
    }
  })();

  return res.json({
    success: true,
    job: "full-pipeline",
    message: "Full pipeline triggered: Scrape → Interlink → Targeted Enrichment → LLM Enrich",
    startedAt,
  });
});

// ─── 6. Job Status ──────────────────────────────────────────────────
app.get("/cron/status", (_req, res) => {
  const jobs = Object.entries(runningJobs).map(([name, info]) => ({
    job: name,
    status: "running",
    ...info,
  }));

  return res.json({
    success: true,
    timestamp: new Date().toISOString(),
    activeJobs: jobs.length,
    jobs,
  });
});

// Database routes removed in favor of direct ETL pipelining.

// ─── Start server ────────────────────────────────────────────────────
app.listen(PORT, () => {
});

module.exports = app;
