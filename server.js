const express = require("express");
const cors = require("cors");
const { scrapePDPLinks } = require("./scraper");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// ─── Health check ────────────────────────────────────────────────────
app.get("/", (_req, res) => {
  res.json({
    service: "PDP Link Scraper",
    version: "1.0.0",
    usage: "GET /scrape?url=<encoded_url>",
    example: "/scrape?url=https://www.amazon.in/s?k=shoes",
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

// ─── Start server ────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════════════════╗
║          PDP Link Scraper — Ready 🚀            ║
╠══════════════════════════════════════════════════╣
║  Server  : http://localhost:${PORT}                ║
║  Scrape  : GET /scrape?url=<encoded_url>        ║
║  Example : /scrape?url=https://amazon.in/s?k=tv ║
╚══════════════════════════════════════════════════╝
  `);
});

module.exports = app;
