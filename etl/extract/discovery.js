const axios = require("axios");
const cheerio = require("cheerio");
const { scrapePDPLinks, isPDPLink } = require("../../scraper");
const {
  PLATFORM_CONFIG,
  detectPlatform,
  isPlatformMedicineUrl,
  isPlatformUrl,
} = require("./platforms");

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

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
  if (EXCLUDED_RESULT_PATTERNS.some((pattern) => pattern.test(url))) return false;
  return isPDPLink(url) || MEDICINE_INFO_PATTERNS.some((pattern) => pattern.test(url));
}

function decodeSearchResultUrl(rawHref) {
  if (!rawHref) return null;

  try {
    const parsed = new URL(rawHref, "https://html.duckduckgo.com");

    if (parsed.hostname.includes("duckduckgo.com")) {
      const uddg = parsed.searchParams.get("uddg");
      if (uddg) {
        return normalizeUrl(decodeURIComponent(uddg));
      }
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
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return Number.POSITIVE_INFINITY;
  }

  return parsed;
}

function buildDiscoveredItem(url, platform, discoveryMethod, sourceUrl = null) {
  return {
    url,
    platform,
    discoveryMethod,
    ...(sourceUrl ? { sourceUrl } : {}),
  };
}

function dedupeDiscoveredItems(items) {
  const deduped = new Map();

  items.forEach((item) => {
    const normalized = normalizeUrl(item.url);
    if (!normalized || deduped.has(normalized)) return;

    deduped.set(normalized, {
      ...item,
      url: normalized,
    });
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

async function searchPlatformViaWeb(query, platform, options = {}) {
  const { timeout = 15000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const domainQuery = platform.domains[0];
  const searchQuery = `site:${domainQuery} "${query}" medicine`;

  const response = await axios.get("https://html.duckduckgo.com/html/", {
    timeout,
    params: { q: searchQuery },
    headers: {
      "User-Agent": UA,
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });

  const $ = cheerio.load(response.data);
  const discovered = [];
  const seen = new Set();

  $("a[href]").each((_, anchor) => {
    if (discovered.length >= perPlatformLimit) return false;

    const decodedUrl = decodeSearchResultUrl($(anchor).attr("href"));
    if (!decodedUrl || seen.has(decodedUrl)) return undefined;
    if (!isPlatformUrl(decodedUrl, platform.id)) return undefined;
    if (!isMedicineResultUrl(decodedUrl)) return undefined;
    if (!isPlatformMedicineUrl(decodedUrl, platform.id, query)) return undefined;

    seen.add(decodedUrl);
    discovered.push({
      url: decodedUrl,
      platform: platform.id,
      discoveryMethod: "web-search",
    });

    return undefined;
  });

  return discovered;
}

async function discoverPharmEasyUrls(query, options = {}) {
  const { timeout = 15000 } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const apiUrl = `https://pharmeasy.in/api/search/searchTypeAhead?intent_id=&q=${encodeURIComponent(
    query
  )}`;
  const searchUrl = `https://pharmeasy.in/search/all?name=${encodeURIComponent(query)}`;
  const discovered = [];
  const seen = new Set();

  try {
    const response = await axios.get(apiUrl, {
      timeout,
      headers: {
        "User-Agent": UA,
        Accept: "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });

    const products = response.data?.data?.products || [];
    products.forEach((product) => {
      if (discovered.length >= perPlatformLimit) return;

      const slug = String(product?.slug || "").trim();
      const productType = Number(product?.productType);
      if (!slug || ![1, 2].includes(productType)) return;

      const url =
        productType === 1
          ? `https://pharmeasy.in/online-medicine-order/${slug}`
          : `https://pharmeasy.in/health-care/products/${slug}`;

      if (
        !isPlatformMedicineUrl(url, "pharmeasy", query, {
          candidateText: product?.name,
          skipQueryMatch: false,
        })
      ) {
        return;
      }

      pushDiscoveredUrl(
        discovered,
        seen,
        url,
        {
          platform: "pharmeasy",
          discoveryMethod: "platform-api",
          sourceUrl: apiUrl,
        },
        perPlatformLimit
      );
    });
  } catch (error) {
    console.warn(`[DISCOVERY] PharmEasy API search failed: ${error.message}`);
  }

  if (discovered.length >= perPlatformLimit) {
    return discovered;
  }

  try {
    const response = await axios.get(searchUrl, {
      timeout,
      headers: {
        "User-Agent": UA,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });

    const matches = response.data.match(PHARMEASY_HTML_LINK_PATTERN) || [];
    matches.forEach((match) => {
      if (discovered.length >= perPlatformLimit) return;
      const url = new URL(match, "https://pharmeasy.in").href;

      if (
        !isPlatformMedicineUrl(url, "pharmeasy", query, {
          candidateText: match,
        })
      ) {
        return;
      }

      pushDiscoveredUrl(
        discovered,
        seen,
        url,
        {
          platform: "pharmeasy",
          discoveryMethod: "platform-search-html",
          sourceUrl: searchUrl,
        },
        perPlatformLimit
      );
    });
  } catch (error) {
    console.warn(`[DISCOVERY] PharmEasy HTML search failed: ${error.message}`);
  }

  return discovered;
}

async function discoverNetmedsUrls(query, options = {}) {
  const { timeout = 15000, mode = "auto" } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);
  const searchUrl = `https://www.netmeds.com/products?q=${encodeURIComponent(
    query
  )}&departments=medicine`;
  const discovered = [];
  const seen = new Set();

  try {
    const response = await axios.get(searchUrl, {
      timeout,
      headers: {
        "User-Agent": UA,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });

    const slugMatches = response.data.matchAll(NETMEDS_PRODUCT_SLUG_PATTERN);
    for (const match of slugMatches) {
      if (discovered.length >= perPlatformLimit) break;
      const slug = match[1];
      const url = `https://www.netmeds.com/product/${slug}`;

      if (
        !isPlatformMedicineUrl(url, "netmeds", query, {
          skipQueryMatch: true,
        })
      ) {
        continue;
      }

      pushDiscoveredUrl(
        discovered,
        seen,
        url,
        {
          platform: "netmeds",
          discoveryMethod: "platform-state",
          sourceUrl: searchUrl,
        },
        perPlatformLimit
      );
    }
  } catch (error) {
    console.warn(`[DISCOVERY] Netmeds search state parsing failed: ${error.message}`);
  }

  if (discovered.length > 0 || mode === "fast") {
    return discovered;
  }

  try {
    const result = await scrapePDPLinks(searchUrl, {
      timeout,
      mode,
      sameDomain: false,
    });

    result.pdpLinks.forEach((link) => {
      if (discovered.length >= perPlatformLimit) return;

      if (
        !isPlatformMedicineUrl(link.url, "netmeds", query, {
          skipQueryMatch: true,
        })
      ) {
        return;
      }

      pushDiscoveredUrl(
        discovered,
        seen,
        link.url,
        {
          platform: "netmeds",
          discoveryMethod: "platform-search",
          sourceUrl: searchUrl,
        },
        perPlatformLimit
      );
    });
  } catch (error) {
    console.warn(`[DISCOVERY] Netmeds browser fallback failed: ${error.message}`);
  }

  return discovered;
}

async function discoverFromPlatformSearchPages(query, options = {}) {
  const {
    timeout = 15000,
    mode = "auto",
    platformIds,
  } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);

  const platforms = platformIds
    ? PLATFORM_CONFIG.filter((platform) => platformIds.includes(platform.id))
    : PLATFORM_CONFIG;

  const discovered = [];

  for (const platform of platforms) {
    for (const buildSearchUrl of platform.searchUrls) {
      try {
        if (platform.id === "pharmeasy") {
          discovered.push(
            ...(
              await discoverPharmEasyUrls(query, {
                timeout,
                perPlatformLimit,
              })
            )
          );
          break;
        }

        if (platform.id === "netmeds") {
          discovered.push(
            ...(
              await discoverNetmedsUrls(query, {
                timeout,
                mode,
                perPlatformLimit,
              })
            )
          );
          break;
        }

        const searchUrl = buildSearchUrl(query);
        const result = await scrapePDPLinks(searchUrl, {
          timeout,
          mode,
          sameDomain: false,
        });

        result.pdpLinks
          .slice(0, perPlatformLimit)
          .forEach((link) => {
            const detectedPlatform = detectPlatform(link.url) || platform.id;
            if (!isPlatformMedicineUrl(link.url, detectedPlatform, query)) {
              return;
            }

            discovered.push({
              url: link.url,
              platform: detectedPlatform,
              discoveryMethod: "platform-search",
              sourceUrl: searchUrl,
            });
          });
      } catch (error) {
        console.warn(
          `[DISCOVERY] Platform search failed for ${platform.id}: ${error.message}`
        );
      }
    }
  }

  return discovered;
}

async function discoverMedicineUrls(query, options = {}) {
  const {
    includeWebSearch = true,
    timeout = 15000,
    mode = "auto",
    platformIds,
  } = options;
  const perPlatformLimit = normalizeDiscoveryLimit(options.perPlatformLimit);

  const platformDiscovered = await discoverFromPlatformSearchPages(query, {
    timeout,
    mode,
    perPlatformLimit,
    platformIds,
  });

  const webPlatforms = platformIds
    ? PLATFORM_CONFIG.filter((platform) => platformIds.includes(platform.id))
    : PLATFORM_CONFIG;

  const webDiscovered = [];
  if (includeWebSearch) {
    for (const platform of webPlatforms) {
      try {
        const platformResults = await searchPlatformViaWeb(query, platform, {
          timeout,
          perPlatformLimit,
        });
        webDiscovered.push(...platformResults);
      } catch (error) {
        console.warn(
          `[DISCOVERY] Web search failed for ${platform.id}: ${error.message}`
        );
      }
    }
  }

  return dedupeDiscoveredItems([...platformDiscovered, ...webDiscovered]);
}

module.exports = {
  discoverNetmedsUrls,
  discoverPharmEasyUrls,
  discoverMedicineUrls,
  discoverFromPlatformSearchPages,
  dedupeDiscoveredItems,
  isMedicineResultUrl,
  normalizeDiscoveryLimit,
};
