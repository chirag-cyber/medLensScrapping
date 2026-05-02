/**
 * Medicine URL Discovery Module
 *
 * Tiered discovery strategy per platform:
 *   Tier 1: Internal API (fastest, most structured)
 *   Tier 2: Stealth browser rendering (for SPAs)
 *   Tier 3: Web search fallback (last resort)
 */

const axios = require("axios");
const cheerio = require("cheerio");
const { scrapePDPLinks, isPDPLink } = require("../../scraper");
const {
  PLATFORM_CONFIG,
  detectPlatform,
  isPlatformMedicineUrl,
  isPlatformUrl,
} = require("./platforms");
const {
  withStealthPage,
  setupApiInterception,
  extractLinksFromPage,
} = require("./stealthBrowser");

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function safeGet(url, config = {}) {
  const maxRetries = 2;
  const backoffMs = 20000;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    try {
      return await axios.get(url, config);
    } catch (error) {
      if (error.response?.status === 429 && attempt < maxRetries) {
        await sleep(backoffMs + Math.random() * 5000);
        continue;
      }
      throw error;
    }
  }
}

const MEDICINE_INFO_PATTERNS = [
  /\/drugs?\//i,
  /\/medicines?\//i,
  /\/medicine\//i,
  /\/salt-information\//i,
  /\/composition\//i,
  /\/uses?\//i,
  /\/online-medicine-order\//i,
  /\/otc\//i,
  /\/non-prescriptions?\//i,
  /\/prescriptions?\//i,
];

const EXCLUDED_RESULT_PATTERNS = [
  /\/diagnostics?\//i,
  /\/lab-tests?\//i,
  /\/tests?\//i,
  /\/profile\//i,
  /\/health-articles?\//i,
  /\/articles?\//i,
  /\/blog\//i,
  /\/doctor\//i,
  /\/consult/i,
  /\/clinic/i,
];

const PHARMEASY_HTML_LINK_PATTERN =
  /\/(?:online-medicine-order|health-care\/products)\/[a-z0-9-]+/gi;
const NETMEDS_PRODUCT_SLUG_PATTERN = /"slug":"([a-z0-9-]+-\d{5,})"/gi;

// ─── Shared Helpers ─────────────────────────────────────────────────

function normalizeUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    return parsed.href;
  } catch {
    return null;
  }
}

function isMedicineResultUrl(url) {
  if (!url) return false;
  if (EXCLUDED_RESULT_PATTERNS.some((p) => p.test(url))) return false;
  return isPDPLink(url) || MEDICINE_INFO_PATTERNS.some((p) => p.test(url));
}

function decodeSearchResultUrl(rawHref) {
  if (!rawHref) return null;
  try {
    const parsed = new URL(rawHref, "https://html.duckduckgo.com");
    if (parsed.hostname.includes("duckduckgo.com")) {
      const uddg = parsed.searchParams.get("uddg");
      if (uddg) return normalizeUrl(decodeURIComponent(uddg));
    }
    return normalizeUrl(parsed.href);
  } catch {
    return normalizeUrl(rawHref);
  }
}

function normalizeDiscoveryLimit(limit) {
  if (limit === null || limit === undefined || limit === "" || limit === "all") {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = Number.parseInt(limit, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : Number.POSITIVE_INFINITY;
}

function buildDiscoveredItem(url, platform, discoveryMethod, sourceUrl = null) {
  return { url, platform, discoveryMethod, ...(sourceUrl ? { sourceUrl } : {}) };
}

function dedupeDiscoveredItems(items) {
  const deduped = new Map();
  items.forEach((item) => {
    const normalized = normalizeUrl(item.url);
    if (!normalized || deduped.has(normalized)) return;
    deduped.set(normalized, { ...item, url: normalized });
  });
  return Array.from(deduped.values());
}

function pushDiscoveredUrl(discovered, seen, url, meta, limit) {
  const normalized = normalizeUrl(url);
  if (!normalized || seen.has(normalized) || discovered.length >= limit) return false;
  seen.add(normalized);
  discovered.push(buildDiscoveredItem(normalized, meta.platform, meta.discoveryMethod, meta.sourceUrl));
  return true;
}

function logTier(platform, tier, method, count) {
}

// ─── Web Search (Tier 3 — shared across all platforms) ──────────────

async function searchViaDuckDuckGo(searchQuery, perPlatformLimit, platformId, query) {
  const response = await axios.get("https://html.duckduckgo.com/html/", {
    timeout: 15000,
    params: { q: searchQuery },
    headers: { "User-Agent": UA, Accept: "text/html", "Accept-Language": "en-US,en;q=0.9" },
  });

  const $ = cheerio.load(response.data);
  const discovered = [];
  const seen = new Set();

  $("a[href]").each((_, anchor) => {
    if (discovered.length >= perPlatformLimit) return false;
    const decodedUrl = decodeSearchResultUrl($(anchor).attr("href"));
    if (!decodedUrl || seen.has(decodedUrl)) return;
    if (!isPlatformUrl(decodedUrl, platformId)) return;
    if (!isMedicineResultUrl(decodedUrl)) return;
    if (!isPlatformMedicineUrl(decodedUrl, platformId, query)) return;
    seen.add(decodedUrl);
    discovered.push({ url: decodedUrl, platform: platformId, discoveryMethod: "web-search" });
  });

  return discovered;
}

async function searchViaBing(searchQuery, perPlatformLimit, platformId, query) {
  const response = await axios.get("https://www.bing.com/search", {
    timeout: 15000,
    params: { q: searchQuery },
    headers: { "User-Agent": UA, Accept: "text/html", "Accept-Language": "en-US,en;q=0.9" },
  });

  const $ = cheerio.load(response.data);
  const discovered = [];
  const seen = new Set();

  $("a[href]").each((_, anchor) => {
    if (discovered.length >= perPlatformLimit) return false;
    const rawHref = $(anchor).attr("href");
    const url = normalizeUrl(rawHref);
    if (!url || seen.has(url)) return;
    if (!isPlatformUrl(url, platformId)) return;
    if (!isMedicineResultUrl(url)) return;
    if (!isPlatformMedicineUrl(url, platformId, query)) return;
    seen.add(url);
    discovered.push({ url, platform: platformId, discoveryMethod: "web-search-bing" });
  });

  return discovered;
}

async function searchPlatformViaWeb(query, platform, options = {}) {
  // Limit web search fallback to a strict maximum of 3 URLs to avoid false positives
  const perPlatformLimit = Math.min(3, normalizeDiscoveryLimit(options.perPlatformLimit));
  const domainQuery = platform.domains[0];
  const searchQuery = `site:${domainQuery} "${query}" medicine`;

  // Try DuckDuckGo first
  try {
    const results = await searchViaDuckDuckGo(searchQuery, perPlatformLimit, platform.id, query);
    if (results.length > 0) return results;
  } catch {
  }

  // Fallback to Bing
  try {
    const results = await searchViaBing(searchQuery, perPlatformLimit, platform.id, query);
    return results;
  } catch {
  }

  return [];
}

// ─── PharmEasy (API-first — already working) ────────────────────────

async function discoverPharmEasyUrls(query, options = {}) {
  const { timeout = 15000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const apiUrl = `https://pharmeasy.in/api/search/searchTypeAhead?intent_id=&q=${encodeURIComponent(query)}`;
  const searchUrl = `https://pharmeasy.in/search/all?name=${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: API with proper headers
  try {
    const response = await safeGet(apiUrl, {
      timeout,
      headers: {
        "User-Agent": UA,
        Accept: "application/json,text/plain,*/*",
        Referer: "https://pharmeasy.in/",
        Origin: "https://pharmeasy.in",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });
    const products = response.data?.data?.products || [];
    products.forEach((product) => {
      if (discovered.length >= perPlatformLimit) return;
      const slug = String(product?.slug || "").trim();
      const productType = Number(product?.productType);
      if (!slug) return;

      // productType 0 = brand-level (needs search redirect), 1 = prescription, 2 = OTC
      let url;
      if (productType === 1) {
        url = `https://pharmeasy.in/online-medicine-order/${slug}`;
      } else if (productType === 2) {
        url = `https://pharmeasy.in/health-care/products/${slug}`;
      } else {
        // Type 0: brand/category — skip, not a product page
        return;
      }

      if (!isPlatformMedicineUrl(url, "pharmeasy", query, { candidateText: product?.name, skipQueryMatch: true })) return;
      pushDiscoveredUrl(discovered, seen, url, { platform: "pharmeasy", discoveryMethod: "platform-api", sourceUrl: apiUrl }, perPlatformLimit);
    });
    logTier("pharmeasy", 1, "API", discovered.length);
    if (discovered.length > 0) return discovered;
  } catch {
  }

  // Tier 2: HTML parsing
  try {
    const response = await axios.get(searchUrl, { timeout, headers: { "User-Agent": UA, Referer: "https://pharmeasy.in/" } });
    const matches = response.data.match(PHARMEASY_HTML_LINK_PATTERN) || [];
    matches.forEach((match) => {
      if (discovered.length >= perPlatformLimit) return;
      const url = new URL(match, "https://pharmeasy.in").href;
      if (!isPlatformMedicineUrl(url, "pharmeasy", query, { candidateText: match })) return;
      pushDiscoveredUrl(discovered, seen, url, { platform: "pharmeasy", discoveryMethod: "platform-search-html", sourceUrl: searchUrl }, perPlatformLimit);
    });
    logTier("pharmeasy", 2, "HTML", discovered.length);
  } catch {
  }

  return discovered;
}

// ─── Netmeds (HTML state + browser fallback) ────────────────────────

async function discoverNetmedsUrls(query, options = {}) {
  const { timeout = 15000, mode = "auto" } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.netmeds.com/products?q=${encodeURIComponent(query)}&departments=medicine`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: HTML state parsing
  try {
    const response = await axios.get(searchUrl, { timeout, headers: { "User-Agent": UA } });
    const slugMatches = response.data.matchAll(NETMEDS_PRODUCT_SLUG_PATTERN);
    for (const match of slugMatches) {
      if (discovered.length >= perPlatformLimit) break;
      const url = `https://www.netmeds.com/product/${match[1]}`;
      if (!isPlatformMedicineUrl(url, "netmeds", query, { skipQueryMatch: true })) continue;
      pushDiscoveredUrl(discovered, seen, url, { platform: "netmeds", discoveryMethod: "platform-state", sourceUrl: searchUrl }, perPlatformLimit);
    }
    logTier("netmeds", 1, "HTML-state", discovered.length);
    if (discovered.length > 0 || mode === "fast") return discovered;
  } catch {
  }

  // Tier 2: Browser fallback
  try {
    const result = await scrapePDPLinks(searchUrl, { timeout, mode, sameDomain: false });
    result.pdpLinks.forEach((link) => {
      if (discovered.length >= perPlatformLimit) return;
      if (!isPlatformMedicineUrl(link.url, "netmeds", query, { skipQueryMatch: true })) return;
      pushDiscoveredUrl(discovered, seen, link.url, { platform: "netmeds", discoveryMethod: "platform-browser", sourceUrl: searchUrl }, perPlatformLimit);
    });
    logTier("netmeds", 2, "browser", discovered.length);
  } catch {
  }

  return discovered;
}

// ─── 1mg (Stealth browser + DOM extraction) ─────────────────────────

async function discover1mgUrls(query, options = {}) {
  const { timeout = 30000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.1mg.com/search/all?name=${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: Stealth browser — intercept API calls + extract DOM links
  try {
    await withStealthPage(searchUrl, async (page) => {
      // Wait for product cards to render
      try {
        await page.waitForSelector('a[href*="/drugs/"], a[href*="/otc/"], a[href*="/otc-product/"]', { timeout: 10000 });
      } catch { /* proceed anyway */ }

      const links = await extractLinksFromPage(page,
        'a[href*="/drugs/"], a[href*="/otc/"], a[href*="/otc-product/"]'
      );

      links.forEach((url) => {
        if (discovered.length >= perPlatformLimit) return;
        const normalized = normalizeUrl(url);
        if (!normalized || seen.has(normalized)) return;
        if (!isPlatformMedicineUrl(normalized, "1mg", query, { skipQueryMatch: true })) return;
        seen.add(normalized);
        discovered.push(buildDiscoveredItem(normalized, "1mg", "platform-browser", searchUrl));
      });
    }, { timeout, extraWaitMs: 3000 });

    logTier("1mg", 1, "stealth-browser", discovered.length);
    if (discovered.length > 0) return discovered;
  } catch {
  }

  // Tier 2: Web search fallback
  try {
    const platform = PLATFORM_CONFIG.find((p) => p.id === "1mg");
    if (platform) {
      const webResults = await searchPlatformViaWeb(query, platform, options);
      discovered.push(...webResults);
      logTier("1mg", 2, "web-search", webResults.length);
    }
  } catch {
  }

  return discovered;
}

// ─── Apollo (Stealth browser + DOM extraction) ──────────────────────

async function discoverApolloUrls(query, options = {}) {
  const { timeout = 30000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.apollopharmacy.in/search-medicines/${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: Stealth browser
  try {
    await withStealthPage(searchUrl, async (page) => {
      try {
        await page.waitForSelector('a[href*="/otc/"], a[href*="/medicine/"], a[href*="/product/"]', { timeout: 12000 });
      } catch { /* proceed */ }

      const links = await extractLinksFromPage(page,
        'a[href*="/otc/"], a[href*="/medicine/"], a[href*="/product/"]'
      );

      links.forEach((url) => {
        if (discovered.length >= perPlatformLimit) return;
        const normalized = normalizeUrl(url);
        if (!normalized || seen.has(normalized)) return;
        if (!isPlatformUrl(normalized, "apollo")) return;
        seen.add(normalized);
        discovered.push(buildDiscoveredItem(normalized, "apollo", "platform-browser", searchUrl));
      });
    }, { timeout, extraWaitMs: 4000 });

    logTier("apollo", 1, "stealth-browser", discovered.length);
    if (discovered.length > 0) return discovered;
  } catch {
  }

  // Tier 2: Web search fallback
  try {
    const platform = PLATFORM_CONFIG.find((p) => p.id === "apollo");
    if (platform) {
      const webResults = await searchPlatformViaWeb(query, platform, options);
      discovered.push(...webResults);
      logTier("apollo", 2, "web-search", webResults.length);
    }
  } catch {
  }

  return discovered;
}

// ─── TrueMeds (Stealth browser + API interception) ──────────────────

async function discoverTruemedUrls(query, options = {}) {
  const { timeout = 30000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.truemeds.in/search?q=${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: Stealth browser with API interception + DOM fallback
  try {
    const { launchStealthBrowser } = require("./stealthBrowser");
    let browser = null;
    try {
      browser = await launchStealthBrowser();
      const page = await browser.newPage();
      await page.setUserAgent(UA);
      await page.setViewport({ width: 1440, height: 900 });

      // Set up API interception BEFORE navigation
      const capturedResponses = [];
      setupApiInterception(page, [
        /\/api\//i,
        /\/search/i,
        /\/product/i,
        /\/medicine/i,
        /graphql/i,
      ], capturedResponses);

      await page.goto(searchUrl, { waitUntil: "networkidle2", timeout });
      await new Promise((r) => setTimeout(r, 5000));

      // Try to extract URLs from captured API responses
      for (const resp of capturedResponses) {
        if (!resp.body) continue;
        const json = JSON.stringify(resp.body);
        // Look for medicine URLs in API response
        const urlMatches = json.match(/https?:\/\/www\.truemeds\.in\/(?:medicine|product|otc)\/[a-z0-9-]+/gi) || [];
        urlMatches.forEach((url) => {
          if (discovered.length >= perPlatformLimit) return;
          const normalized = normalizeUrl(url);
          if (!normalized || seen.has(normalized)) return;
          seen.add(normalized);
          discovered.push(buildDiscoveredItem(normalized, "truemeds", "platform-api-intercept", searchUrl));
        });
      }

      // Also try DOM extraction as backup
      if (discovered.length === 0) {
        const links = await page.evaluate(() => {
          const anchors = document.querySelectorAll('a[href]');
          const urls = [];
          anchors.forEach((a) => {
            const href = a.href || a.getAttribute("href");
            if (href && (href.includes("/medicine/") || href.includes("/product/") || href.includes("/otc/"))) {
              urls.push(href);
            }
          });
          return urls;
        });

        links.forEach((url) => {
          if (discovered.length >= perPlatformLimit) return;
          const normalized = normalizeUrl(url);
          if (!normalized || seen.has(normalized)) return;
          if (!isPlatformUrl(normalized, "truemeds")) return;
          seen.add(normalized);
          discovered.push(buildDiscoveredItem(normalized, "truemeds", "platform-browser", searchUrl));
        });
      }
    } finally {
      if (browser) try { await browser.close(); } catch { /* ignore */ }
    }

    logTier("truemeds", 1, "stealth+intercept", discovered.length);
    if (discovered.length > 0) return discovered;
  } catch {
  }

  // Tier 2: Web search fallback
  try {
    const platform = PLATFORM_CONFIG.find((p) => p.id === "truemeds");
    if (platform) {
      const webResults = await searchPlatformViaWeb(query, platform, options);
      discovered.push(...webResults);
      logTier("truemeds", 2, "web-search", webResults.length);
    }
  } catch {
  }

  return discovered;
}

// ─── MedPlus (Stealth browser + DOM extraction + debug) ─────────────

async function discoverMedplusUrls(query, options = {}) {
  const { timeout = 35000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.medplusmart.com/searchProduct?q=${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  // Tier 1: Stealth browser with extra delay + API interception
  try {
    const { launchStealthBrowser } = require("./stealthBrowser");
    let browser = null;
    try {
      browser = await launchStealthBrowser();
      const page = await browser.newPage();
      await page.setUserAgent(UA);
      await page.setViewport({ width: 1440, height: 900 });

      // Set up API interception for product data
      const capturedResponses = [];
      setupApiInterception(page, [
        /\/api\//i,
        /searchProduct/i,
        /\/product/i,
        /graphql/i,
      ], capturedResponses);

      await page.goto(searchUrl, { waitUntil: "networkidle0", timeout });

      // Extended wait for MedPlus's slow SPA rendering
      await new Promise((r) => setTimeout(r, 6000));

      // Auto-scroll to trigger lazy content
      const { autoScroll } = require("./stealthBrowser");
      await autoScroll(page);
      await new Promise((r) => setTimeout(r, 3000));

      // Try API interception results first
      for (const resp of capturedResponses) {
        if (!resp.body) continue;
        const json = JSON.stringify(resp.body);
        const urlMatches = json.match(/https?:\/\/www\.medplusmart\.com\/(?:product|pharma|medicine)\/[a-z0-9-]+/gi) || [];
        urlMatches.forEach((url) => {
          if (discovered.length >= perPlatformLimit) return;
          const normalized = normalizeUrl(url);
          if (!normalized || seen.has(normalized)) return;
          seen.add(normalized);
          discovered.push(buildDiscoveredItem(normalized, "medplus", "platform-api-intercept", searchUrl));
        });
      }

      // DOM extraction — broad selectors for MedPlus
      if (discovered.length === 0) {
        const links = await page.evaluate(() => {
          const anchors = document.querySelectorAll('a[href]');
          const urls = [];
          anchors.forEach((a) => {
            const href = a.href || a.getAttribute("href");
            if (href && href.includes("medplusmart.com") && (
              href.includes("/product/") || href.includes("/pharma/") ||
              href.includes("/medicine/") || href.includes("/searchProduct")
            )) {
              urls.push(href);
            }
          });
          return urls;
        });

        links.forEach((url) => {
          if (discovered.length >= perPlatformLimit) return;
          const normalized = normalizeUrl(url);
          if (!normalized || seen.has(normalized)) return;
          if (!isPlatformUrl(normalized, "medplus")) return;
          seen.add(normalized);
          discovered.push(buildDiscoveredItem(normalized, "medplus", "platform-browser", searchUrl));
        });
      }

      // Debug: log page title if zero results
      if (discovered.length === 0) {
        const title = await page.title();
        const bodyLen = await page.evaluate(() => document.body.innerText.length);
      }
    } finally {
      if (browser) try { await browser.close(); } catch { /* ignore */ }
    }

    logTier("medplus", 1, "stealth+intercept", discovered.length);
    if (discovered.length > 0) return discovered;
  } catch {
  }

  // Tier 2: Web search fallback
  try {
    const platform = PLATFORM_CONFIG.find((p) => p.id === "medplus");
    if (platform) {
      const webResults = await searchPlatformViaWeb(query, platform, options);
      discovered.push(...webResults);
      logTier("medplus", 2, "web-search", webResults.length);
    }
  } catch {
  }

  return discovered;
}

// ─── Main Discovery Orchestrator ────────────────────────────────────

async function discoverFromPlatformSearchPages(query, options = {}) {
  const { timeout = 15000, mode = "auto", platformIds } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);

  const platforms = platformIds
    ? PLATFORM_CONFIG.filter((p) => platformIds.includes(p.id))
    : PLATFORM_CONFIG;

  const discovered = [];
  const discoveryFns = {
    pharmeasy: discoverPharmEasyUrls,
    netmeds: discoverNetmedsUrls,
    "1mg": discover1mgUrls,
    apollo: discoverApolloUrls,
    truemeds: discoverTruemedUrls,
    medplus: discoverMedplusUrls,
  };

  for (const platform of platforms) {
    const fn = discoveryFns[platform.id];
    if (fn) {
      try {
        const results = await fn(query, { timeout, mode, perPlatformLimit });
        discovered.push(...results);
      } catch {
      }
    }
  }

  return discovered;
}

async function discoverMedicineUrls(query, options = {}) {
  const { includeWebSearch = true, timeout = 15000, mode = "auto", platformIds } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);

  const platformDiscovered = await discoverFromPlatformSearchPages(query, {
    timeout, mode, perPlatformLimit, platformIds,
  });

  // Web search as additional fallback for platforms that found nothing
  const webDiscovered = [];
  if (includeWebSearch) {
    const webPlatforms = platformIds
      ? PLATFORM_CONFIG.filter((p) => platformIds.includes(p.id))
      : PLATFORM_CONFIG;

    // Only web-search platforms that got 0 results from direct discovery
    const coveredPlatforms = new Set(platformDiscovered.map((d) => d.platform));
    const uncoveredPlatforms = webPlatforms.filter((p) => !coveredPlatforms.has(p.id));

    for (const platform of uncoveredPlatforms) {
      try {
        const results = await searchPlatformViaWeb(query, platform, { timeout, perPlatformLimit });
        webDiscovered.push(...results);
        if (results.length > 0) {
          logTier(platform.id, 3, "web-search-fallback", results.length);
        }
      } catch {
      }
    }
  }

  return dedupeDiscoveredItems([...platformDiscovered, ...webDiscovered]);
}

module.exports = {
  discoverNetmedsUrls,
  discoverPharmEasyUrls,
  discover1mgUrls,
  discoverApolloUrls,
  discoverTruemedUrls,
  discoverMedplusUrls,
  discoverMedicineUrls,
  discoverFromPlatformSearchPages,
  dedupeDiscoveredItems,
  isMedicineResultUrl,
  normalizeDiscoveryLimit,
};
