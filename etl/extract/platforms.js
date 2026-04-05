const DEFAULT_INCLUDE_PATTERNS = [
  /\/drugs?\//i,
  /\/medicines?\//i,
  /\/medicine\//i,
  /\/online-medicine-order\//i,
  /\/health-care\/products\//i,
  /\/otc(?:-product)?\//i,
  /\/non-prescriptions?\//i,
  /\/prescriptions?\//i,
  /\/product\//i,
  /\/products?\//i,
];

const DEFAULT_EXCLUDE_PATTERNS = [
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

const PLATFORM_CONFIG = [
  {
    id: "1mg",
    domains: ["1mg.com", "www.1mg.com", "tata1mg.com", "www.tata1mg.com"],
    searchUrls: [
      (query) => `https://www.1mg.com/search/all?name=${encodeURIComponent(query)}`,
    ],
    includePatterns: [/\/drugs?\//i, /\/otc(?:-product)?\//i],
  },
  {
    id: "pharmeasy",
    domains: ["pharmeasy.in", "www.pharmeasy.in"],
    searchUrls: [
      (query) => `https://pharmeasy.in/search/all?name=${encodeURIComponent(query)}`,
    ],
    includePatterns: [/\/online-medicine-order\//i, /\/health-care\/products\//i],
    excludePatterns: [/\/diagnostics?\//i, /\/profile\//i, /\/browse\/?$/i],
  },
  {
    id: "netmeds",
    domains: ["netmeds.com", "www.netmeds.com"],
    searchUrls: [
      (query) =>
        `https://www.netmeds.com/products?q=${encodeURIComponent(query)}&departments=medicine`,
    ],
    includePatterns: [/\/product\//i],
  },
  {
    id: "apollo",
    domains: ["apollopharmacy.in", "www.apollopharmacy.in"],
    searchUrls: [
      (query) => `https://www.apollopharmacy.in/search-medicines/${encodeURIComponent(query)}`,
    ],
    includePatterns: [/\/otc(?:-product)?\//i],
  },
  {
    id: "truemeds",
    domains: ["truemeds.in", "www.truemeds.in"],
    searchUrls: [],
    includePatterns: [/\/medicine\//i, /\/drugs?\//i, /\/products?\//i],
  },
  {
    id: "medplus",
    domains: ["medplusmart.com", "www.medplusmart.com"],
    searchUrls: [],
    includePatterns: [/\/otc(?:-product)?\//i, /\/products?\//i, /\/medicine\//i],
  },
];

function getPlatformConfigById(platformId) {
  return PLATFORM_CONFIG.find((platform) => platform.id === platformId) || null;
}

function detectPlatform(url) {
  if (!url) return "unknown";

  try {
    const hostname = new URL(url).hostname.toLowerCase();
    const match = PLATFORM_CONFIG.find((platform) =>
      platform.domains.some(
        (domain) => hostname === domain || hostname.endsWith(`.${domain}`)
      )
    );

    return match ? match.id : hostname.replace(/^www\./, "");
  } catch {
    return "unknown";
  }
}

function isPlatformUrl(url, platformId) {
  const platform = getPlatformConfigById(platformId);
  if (!platform) return false;

  try {
    const hostname = new URL(url).hostname.toLowerCase();
    return platform.domains.some(
      (domain) => hostname === domain || hostname.endsWith(`.${domain}`)
    );
  } catch {
    return false;
  }
}

function tokenizeQuery(query) {
  return String(query || "")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2);
}

function textMatchesQuery(value, query) {
  const tokens = tokenizeQuery(query);
  if (tokens.length === 0) return true;

  const haystack = String(value || "").toLowerCase();
  return tokens.some((token) => haystack.includes(token));
}

function urlMatchesQuery(url, query) {
  const tokens = tokenizeQuery(query);
  if (tokens.length === 0) return true;

  try {
    const parsed = new URL(url);
    const haystack = decodeURIComponent(
      `${parsed.hostname}${parsed.pathname}${parsed.search}`.toLowerCase()
    );
    return tokens.some((token) => haystack.includes(token));
  } catch {
    const lowered = String(url).toLowerCase();
    return tokens.some((token) => lowered.includes(token));
  }
}

function isPlatformMedicineUrl(url, platformId, query = "", options = {}) {
  const { skipQueryMatch = false, candidateText = "" } = options;
  const platform = getPlatformConfigById(platformId);
  const includePatterns = platform?.includePatterns || DEFAULT_INCLUDE_PATTERNS;
  const excludePatterns = [...DEFAULT_EXCLUDE_PATTERNS, ...(platform?.excludePatterns || [])];

  if (!isPlatformUrl(url, platformId)) return false;
  if (excludePatterns.some((pattern) => pattern.test(url))) return false;
  if (!includePatterns.some((pattern) => pattern.test(url))) return false;
  if (!skipQueryMatch && !urlMatchesQuery(url, query) && !textMatchesQuery(candidateText, query)) {
    return false;
  }

  return true;
}

module.exports = {
  DEFAULT_EXCLUDE_PATTERNS,
  DEFAULT_INCLUDE_PATTERNS,
  PLATFORM_CONFIG,
  detectPlatform,
  getPlatformConfigById,
  isPlatformMedicineUrl,
  isPlatformUrl,
  textMatchesQuery,
};
