const axios = require("axios");
const cheerio = require("cheerio");
const puppeteer = require("puppeteer");
const { URL } = require("url");

// ─── PDP URL Patterns ───────────────────────────────────────────────
// Heuristics to identify product detail page links across e-commerce sites.
const PDP_PATTERNS = [
  // ─── General E-commerce ──────────────────────────────────────────
  /\/products?\//i,
  /\/p\//i,
  /\/dp\//i,
  /\/item\//i,
  /\/pdp\//i,
  /\/buy\//i,
  /\/listing\//i,
  /\/itm\//i,
  /\/ip\//i,
  /\/catalog\/product/i,
  /\/shop\/[\w-]+\/[\w-]+/i,
  /\/[\w-]+-pid-/i,
  /\/[\w-]+\/[\w-]+\/[\w-]+-\d{4,}/i,

  // ─── Medical / Pharma Sites ──────────────────────────────────────
  /\/otc\/[\w-]+/i,
  /\/non-prescriptions?\//i,
  /\/prescriptions?\//i,
  /\/online-medicine-order\/[\w-]+/i,
  /\/drugs\/[\w-]+/i,
  /\/lab-tests?\/[\w-]+/i,
  /\/otc-product\/[\w-]+/i,
];

// Patterns to EXCLUDE (category pages, search pages, etc.)
const EXCLUDE_PATTERNS = [
  /\/search/i,
  /\/category\/?$/i,
  /\/categories/i,
  /\/collections?\/?$/i,
  /\/cart/i,
  /\/checkout/i,
  /\/account/i,
  /\/login/i,
  /\/signup/i,
  /\/register/i,
  /\/help/i,
  /\/faq/i,
  /\/about/i,
  /\/contact/i,
  /\/terms/i,
  /\/privacy/i,
  /\/policies/i,
  /\/blog/i,
  /\/news/i,
  /javascript:/i,
  /mailto:/i,
  /tel:/i,
  /#$/,
];

/**
 * Check if a URL looks like a PDP link.
 */
function isPDPLink(href) {
  if (!href || typeof href !== "string") return false;
  if (EXCLUDE_PATTERNS.some((p) => p.test(href))) return false;
  return PDP_PATTERNS.some((p) => p.test(href));
}

/**
 * Resolve a potentially relative URL against a base URL.
 */
function resolveUrl(href, baseUrl) {
  try {
    return new URL(href, baseUrl).href;
  } catch {
    return null;
  }
}

/**
 * Extract PDP links from raw HTML string.
 */
function extractLinksFromHTML(html, targetUrl, sameDomain) {
  const $ = cheerio.load(html);
  const parsedUrl = new URL(targetUrl);
  const allLinks = new Set();
  const pdpLinks = new Set();

  $("a[href]").each((_, el) => {
    const rawHref = $(el).attr("href");
    if (!rawHref) return;

    const absoluteUrl = resolveUrl(rawHref.trim(), targetUrl);
    if (!absoluteUrl) return;

    if (sameDomain) {
      try {
        if (new URL(absoluteUrl).hostname !== parsedUrl.hostname) return;
      } catch {
        return;
      }
    }

    let cleanUrl;
    try {
      const u = new URL(absoluteUrl);
      u.hash = "";
      cleanUrl = u.href;
    } catch {
      cleanUrl = absoluteUrl;
    }

    allLinks.add(cleanUrl);
    if (isPDPLink(cleanUrl)) {
      pdpLinks.add(cleanUrl);
    }
  });

  return { allLinks, pdpLinks };
}

// ─── MODE 1: Fast fetch with Axios ──────────────────────────────────
async function fetchWithAxios(targetUrl, timeout) {
  const response = await axios.get(targetUrl, {
    timeout,
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      Accept:
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
      "Accept-Encoding": "gzip, deflate, br",
      Connection: "keep-alive",
      "Cache-Control": "no-cache",
    },
    maxRedirects: 5,
  });
  return response.data;
}

// ─── MODE 2: Headless browser with Puppeteer ────────────────────────
async function fetchWithPuppeteer(targetUrl, timeout) {
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: "new",
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
      ],
    });

    const page = await browser.newPage();

    // Set a realistic viewport and user agent
    await page.setViewport({ width: 1440, height: 900 });
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    );

    // Navigate and wait for network to settle
    await page.goto(targetUrl, {
      waitUntil: "networkidle2",
      timeout,
    });

    // Scroll down to trigger lazy-loaded content
    await autoScroll(page);

    // Wait a bit for any final renders
    await new Promise((r) => setTimeout(r, 2000));

    // Get the fully rendered HTML
    const html = await page.content();
    return html;
  } finally {
    if (browser) await browser.close();
  }
}

/**
 * Auto-scroll the page to trigger lazy-loaded content.
 */
async function autoScroll(page) {
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let totalHeight = 0;
      const distance = 400;
      const timer = setInterval(() => {
        const scrollHeight = document.body.scrollHeight;
        window.scrollBy(0, distance);
        totalHeight += distance;
        if (totalHeight >= scrollHeight) {
          clearInterval(timer);
          resolve();
        }
      }, 200);
      // Safety timeout
      setTimeout(() => {
        clearInterval(timer);
        resolve();
      }, 10000);
    });
  });
}

/**
 * Scrape PDP links from a given URL.
 * Uses Axios first (fast). If 0 PDP links found, retries with Puppeteer (handles JS-rendered pages).
 *
 * @param {string} targetUrl       - The URL to scrape.
 * @param {object} options
 * @param {number}  options.timeout    - Request timeout in ms (default 15000).
 * @param {boolean} options.sameDomain - Only keep links on same domain (default true).
 * @param {string}  options.mode       - "auto" | "fast" | "browser" (default "auto").
 * @returns {Promise<object>} JSON result with extracted PDP links.
 */
async function scrapePDPLinks(targetUrl, options = {}) {
  const {
    timeout = 15000,
    sameDomain = true,
    mode = "auto",
  } = options;

  // Validate URL
  try {
    new URL(targetUrl);
  } catch {
    throw new Error(`Invalid URL: ${targetUrl}`);
  }

  let html;
  let usedMode = "fast";

  // ─── FAST mode: try Axios first ────────────────────────────────
  if (mode === "fast" || mode === "auto") {
    try {
      console.error("  ⚡ Trying fast fetch (Axios)...");
      html = await fetchWithAxios(targetUrl, timeout);
      const { allLinks, pdpLinks } = extractLinksFromHTML(
        html,
        targetUrl,
        sameDomain
      );

      // If we found PDP links, return immediately
      if (pdpLinks.size > 0 || mode === "fast") {
        usedMode = "fast";
        return buildResult(targetUrl, allLinks, pdpLinks, usedMode);
      }

      // No PDP links found with fast mode — fall through to browser
      console.error(
        "  ⚠️  No PDP links found with fast fetch. Trying headless browser..."
      );
    } catch (err) {
      if (mode === "fast") {
        throw new Error(
          `Failed to fetch page: ${err.response?.status || err.message}`
        );
      }
      console.error(
        `  ⚠️  Fast fetch failed (${err.response?.status || err.message}). Trying headless browser...`
      );
    }
  }

  // ─── BROWSER mode: use Puppeteer ───────────────────────────────
  try {
    console.error("  🌐 Launching headless browser (Puppeteer)...");
    html = await fetchWithPuppeteer(targetUrl, timeout + 15000);
    usedMode = "browser";
    const { allLinks, pdpLinks } = extractLinksFromHTML(
      html,
      targetUrl,
      sameDomain
    );
    return buildResult(targetUrl, allLinks, pdpLinks, usedMode);
  } catch (err) {
    throw new Error(`Failed to fetch page with browser: ${err.message}`);
  }
}

/**
 * Build the JSON result object.
 */
function buildResult(targetUrl, allLinks, pdpLinks, mode) {
  const pdpArray = [...pdpLinks].map((url) => {
    const u = new URL(url);
    return {
      url,
      path: u.pathname + u.search,
    };
  });

  return {
    success: true,
    source: targetUrl,
    mode,
    scrapedAt: new Date().toISOString(),
    totalLinksFound: allLinks.size,
    pdpLinksFound: pdpArray.length,
    pdpLinks: pdpArray,
  };
}

module.exports = { scrapePDPLinks, isPDPLink };
