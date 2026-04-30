/**
 * Stealth Browser Utility
 *
 * Shared Puppeteer wrapper with anti-bot bypass via puppeteer-extra-plugin-stealth.
 * Provides:
 *   - withStealthPage(url, callback, options)  — lifecycle-managed stealth page
 *   - interceptApiCalls(page, patterns)        — capture internal API responses
 *   - launchStealthBrowser()                   — raw browser launch
 */

const puppeteerExtra = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");

// Apply stealth plugin once at module load
puppeteerExtra.use(StealthPlugin());

// ─── User-Agent Pool ────────────────────────────────────────────────
const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
];

const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1440, height: 900 },
  { width: 1536, height: 864 },
  { width: 1366, height: 768 },
];

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ─── Browser Launch ─────────────────────────────────────────────────

/**
 * Launch a stealth-enabled Puppeteer browser.
 *
 * @param {object} options
 * @param {boolean} options.headless - Run headless (default true)
 * @returns {Promise<Browser>}
 */
async function launchStealthBrowser(options = {}) {
  const { headless = true } = options;

  return puppeteerExtra.launch({
    headless: headless ? "new" : false,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
      "--disable-infobars",
      "--window-size=1440,900",
      "--disable-gpu",
    ],
    ignoreDefaultArgs: ["--enable-automation"],
  });
}

// ─── Page Lifecycle ─────────────────────────────────────────────────

/**
 * Execute a callback with a stealth-enabled page. Handles browser lifecycle.
 *
 * @param {string} url - URL to navigate to
 * @param {function(page, browser): Promise<T>} callback - Receives (page, browser)
 * @param {object} options
 * @param {number} options.timeout - Navigation timeout in ms (default 30000)
 * @param {string} options.waitUntil - Puppeteer wait condition (default "networkidle2")
 * @param {boolean} options.scroll - Auto-scroll to trigger lazy content (default true)
 * @param {number} options.extraWaitMs - Extra wait after load (default 2000)
 * @returns {Promise<T>} - Whatever the callback returns
 */
async function withStealthPage(url, callback, options = {}) {
  const {
    timeout = 30000,
    waitUntil = "networkidle2",
    scroll = true,
    extraWaitMs = 2000,
  } = options;

  let browser = null;
  try {
    browser = await launchStealthBrowser();
    const page = await browser.newPage();

    // Randomize fingerprint
    await page.setUserAgent(randomItem(USER_AGENTS));
    await page.setViewport(randomItem(VIEWPORTS));

    // Set extra headers to look more human
    await page.setExtraHTTPHeaders({
      "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    });

    // Navigate
    await page.goto(url, { waitUntil, timeout });

    // Extra wait for SPA hydration
    await new Promise((r) => setTimeout(r, extraWaitMs));

    // Auto-scroll to trigger lazy-loaded content
    if (scroll) {
      await autoScroll(page);
      await new Promise((r) => setTimeout(r, 1000));
    }

    // Execute the caller's extraction logic
    return await callback(page, browser);
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch {
        /* ignore close errors */
      }
    }
  }
}

// ─── API Interception ───────────────────────────────────────────────

/**
 * Set up response interception on a page. Captured responses matching
 * any of the URL patterns are collected and returned after navigation.
 *
 * Usage:
 *   const captured = [];
 *   const page = await browser.newPage();
 *   setupApiInterception(page, [/api\/search/i], captured);
 *   await page.goto(url);
 *   // captured[] now has { url, status, body } entries
 *
 * @param {Page} page - Puppeteer page
 * @param {RegExp[]} urlPatterns - Patterns to match response URLs
 * @param {Array} capturedResponses - Array to push captured responses into
 */
function setupApiInterception(page, urlPatterns, capturedResponses) {
  page.on("response", async (response) => {
    const responseUrl = response.url();
    const matchesPattern = urlPatterns.some((pattern) => pattern.test(responseUrl));

    if (!matchesPattern) return;

    try {
      const contentType = response.headers()["content-type"] || "";
      if (!contentType.includes("json")) return;

      const body = await response.json();
      capturedResponses.push({
        url: responseUrl,
        status: response.status(),
        body,
      });
    } catch {
      // Ignore non-JSON or failed response reads
    }
  });
}

// ─── Auto-Scroll ────────────────────────────────────────────────────

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
      }, 150);
      // Safety timeout
      setTimeout(() => {
        clearInterval(timer);
        resolve();
      }, 8000);
    });
  });
}

// ─── Extract Links from Rendered Page ───────────────────────────────

/**
 * Extract all hrefs matching a CSS selector from a rendered page.
 *
 * @param {Page} page - Puppeteer page
 * @param {string} selector - CSS selector for anchor elements
 * @returns {Promise<string[]>} - Array of absolute URLs
 */
async function extractLinksFromPage(page, selector) {
  return page.evaluate((sel) => {
    const anchors = document.querySelectorAll(sel);
    const urls = [];
    anchors.forEach((a) => {
      const href = a.href || a.getAttribute("href");
      if (href && href.startsWith("http")) urls.push(href);
    });
    return urls;
  }, selector);
}

module.exports = {
  launchStealthBrowser,
  withStealthPage,
  setupApiInterception,
  extractLinksFromPage,
  autoScroll,
};
