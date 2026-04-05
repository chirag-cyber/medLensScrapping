const axios = require("axios");
const cheerio = require("cheerio");
const puppeteer = require("puppeteer");
const { detectPlatform } = require("./etl/extract/platforms");
const { extractDosage } = require("./etl/transform/transformer");

// ─── Common User-Agent ──────────────────────────────────────────────
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

// ─── CSS Selector Maps (site-agnostic, ordered by likelihood) ───────
const SELECTORS = {
  name: [
    "h1.productName",
    "h1.DrugHeader__drug-name",
    "h1.ProductTitle__product-title",
    "h1[class*='product']",
    "h1[class*='title']",
    "h1[class*='name']",
    "#productTitle",
    "#title span",
    "h1",
    '[data-testid="product-title"]',
    ".product-name h1",
    ".product-title",
    ".product_title",
    "h1.heading",
  ],
  price: [
    // Medical / Pharma
    ".DrugPriceBox__best-price",
    ".DrugPriceBox__price",
    ".ProductPriceContainer__our-price",
    ".PriceBoxPlan498__offer-price",
    ".final-price",
    '[class*="offer-price"]',
    '[class*="best-price"]',
    '[class*="sale-price"]',
    '[class*="selling-price"]',
    // General e-commerce
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    ".a-price .a-offscreen",
    ".a-price-whole",
    "span[class*='price'] span",
    ".product-price",
    ".sale-price",
    ".selling-price",
    ".special-price .price",
    '[data-testid="price"]',
    ".price-current",
    ".discounted-price",
    '[class*="discountedPrice"]',
    '[class*="sellingPrice"]',
  ],
  mrp: [
    ".DrugPriceBox__mrp",
    ".ProductPriceContainer__mrp",
    ".PriceBoxPlan498__mrp",
    '[class*="original-price"]',
    '[class*="mrp"]',
    ".old-price .price",
    ".original-price",
    ".list-price",
    "#listPrice",
    "#priceblock_ourprice + .priceBlockStrikePriceString",
    ".a-text-price .a-offscreen",
    ".a-price[data-a-strike] .a-offscreen",
    "s .price",
    "del .price",
    ".price-old",
    '[class*="strikePrice"]',
    '[class*="actualPrice"]',
    "span[style*='line-through']",
  ],
  discount: [
    ".DrugPriceBox__discount",
    ".ProductPriceContainer__discount",
    '[class*="discount"]',
    '[class*="savings"]',
    ".savingsPercentage",
    "#dealprice_savings .priceBlockSavingsString",
    '[data-testid="discount"]',
    ".product-discount",
    ".save-price",
  ],
  quantity: [
    ".DrugHeader__quantity",
    ".ProductTitle__pack-size",
    ".pack-size",
    '[class*="pack-size"]',
    '[class*="packSize"]',
    '[class*="quantity"]',
    '[class*="unit"]',
    '[class*="size-value"]',
    ".product-quantity",
    ".variant-size",
    ".size-text",
    '[data-testid="pack-size"]',
    '[data-testid="quantity"]',
  ],
  brand: [
    ".DrugHeader__manufacturer",
    ".ProductTitle__manufacturer",
    '[class*="brand"]',
    '[class*="manufacturer"]',
    "#bylineInfo",
    ".product-brand",
    '[data-testid="brand"]',
    "a[class*='brand']",
  ],
  manufacturer: [
    ".DrugHeader__manufacturer",
    ".ProductTitle__manufacturer",
    '[class*="manufacturer"]',
    '[class*="manufacture"]',
    '[data-testid="manufacturer"]',
    '.product-manufacturer',
    '.manufacturer-name',
    '.brand-manufacturer',
  ],
  rating: [
    ".RatingDisplay__rating",
    '[class*="rating-num"]',
    '[class*="rating-value"]',
    "#acrPopover",
    ".a-icon-alt",
    '[data-testid="rating"]',
    ".product-rating",
    ".rating-average",
    '[class*="averageRating"]',
  ],
  image: [
    "#imgTagWrapperId img",
    "#landingImage",
    ".product-image img",
    ".ProductImage__image img",
    '[class*="product-image"] img',
    '[data-testid="product-image"] img',
    ".gallery-image img",
    "picture.product img",
    ".zoomable-image img",
    "img[class*='product']",
  ],
  availability: [
    "#availability span",
    ".DrugPriceBox__availability",
    '[class*="availability"]',
    '[class*="in-stock"]',
    '[class*="out-of-stock"]',
    '[data-testid="availability"]',
    ".product-availability",
    ".stock-status",
  ],
  salt: [
    ".saltInfo",
    ".saltComposition",
    ".composition",
    '[class*="salt"]',
    '[class*="composition"]',
    '[data-testid="salt"]',
    '[data-testid="composition"]',
  ],
  description: [
    ".ProductDescription",
    ".description",
    ".product-description",
    '[class*="description"]',
    '[class*="overview"]',
    '[data-testid="description"]',
  ],
};

// ─── Helpers ────────────────────────────────────────────────────────

/**
 * Extract a numeric price from a text string.
 * Handles ₹, $, commas, etc.
 */
function parsePrice(text) {
  if (!text) return null;
  // Match the first sequence of digits with an optional decimal part
  const match = text.replace(/,/g, "").match(/\d+(?:\.\d+)?/);
  if (match) {
    const num = parseFloat(match[0]);
    return isNaN(num) ? null : num;
  }
  return null;
}

/**
 * Extract rating number from text like "4.3 out of 5" or "4.3"
 */
function parseRating(text) {
  if (!text) return null;
  const match = text.match(/([\d.]+)\s*(out of|\/|\s*stars?)?/i);
  if (match) {
    const num = parseFloat(match[1]);
    return isNaN(num) ? null : num;
  }
  return null;
}

/**
 * Extract currency from price text.
 */
function parseCurrency(text) {
  if (!text) return null;
  if (text.includes("₹") || text.toLowerCase().includes("inr")) return "INR";
  if (text.includes("$") && !text.includes("₹")) return "USD";
  if (text.includes("€")) return "EUR";
  if (text.includes("£")) return "GBP";
  return null;
}

function parsePriceNearCurrency(text) {
  if (!text) return null;

  const patterns = [
    /(?:₹|â‚¹)\s*([\d,]+(?:\.\d+)?)/i,
    /\b(?:rs\.?|inr)\s*([\d,]+(?:\.\d+)?)/i,
    /\bprice\b[^₹â‚¹\d]{0,20}(?:₹|â‚¹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)/i,
    /\bat\s+(?:₹|â‚¹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)/i,
  ];

  for (const pattern of patterns) {
    const match = String(text).match(pattern);
    if (!match?.[1]) continue;

    const parsed = parseFloat(match[1].replace(/,/g, ""));
    if (!Number.isNaN(parsed)) return parsed;
  }

  return null;
}

/**
 * Get first matching text from a list of CSS selectors.
 */
function getFirstMatch($, selectors) {
  for (const sel of selectors) {
    const el = $(sel).first();
    if (el.length) {
      const text = el.text().trim();
      if (text) return text;
    }
  }
  return null;
}

/**
 * Get first matching image src from selectors.
 */
function getFirstImageSrc($, selectors) {
  for (const sel of selectors) {
    const el = $(sel).first();
    if (el.length) {
      return (
        el.attr("src") ||
        el.attr("srcset")?.split(",")[0]?.trim().split(" ")[0] ||
        el.attr("data-src") ||
        el.attr("data-srcset")?.split(",")[0]?.trim().split(" ")[0] ||
        el.attr("data-lazy-src") ||
        null
      );
    }
  }
  return null;
}

function toAbsoluteUrl(url, baseUrl) {
  if (!url) return null;

  try {
    const normalized = String(url)
      .replace(/\\u002F/gi, "/")
      .replace(/\\\//g, "/")
      .replace(/&amp;/gi, "&")
      .replace(/^https:\\\//i, "https://")
      .replace(/^http:\\\//i, "http://");
    const resolved = new URL(normalized, baseUrl);
    if (!["http:", "https:"].includes(resolved.protocol)) return null;
    resolved.hash = "";
    return resolved.href;
  } catch {
    return null;
  }
}

function scoreImageCandidate(candidate) {
  const lowered = String(candidate || "").toLowerCase();
  if (!lowered) return Number.NEGATIVE_INFINITY;
  if (lowered.startsWith("data:")) return Number.NEGATIVE_INFINITY;

  let score = 0;

  if (/\.(png|jpg|jpeg|webp)(?:[?#]|$)/i.test(lowered)) score += 2;
  if (/\.(svg)(?:[?#]|$)/i.test(lowered)) score -= 6;

  const positiveHints = [
    "productsnowatermark",
    "/dam/products/",
    "/dam/productsnowatermark/",
    "pharmacy-production-rxs",
    "/products/assets/item/",
    "/item/free/original/",
    "non-watermark",
    "-front-",
    "-back-",
    "tablet",
    "capsule",
    "syrup",
    "medicine",
    "product",
  ];
  const negativeHints = [
    "site-icons",
    "logo",
    "icon",
    "delivery",
    "/blog/",
    "/conditions/",
    "doctor",
    "home.png",
    "placeholder",
    "thumb",
    "avatar",
    "banner",
  ];

  positiveHints.forEach((hint) => {
    if (lowered.includes(hint)) score += 4;
  });
  negativeHints.forEach((hint) => {
    if (lowered.includes(hint)) score -= 5;
  });

  return score;
}

function extractImageCandidatesFromHtml(html, baseUrl) {
  const patterns = [
    /https?:[^"'\s>]+\.(?:png|jpg|jpeg|webp)(?:[^"'\s>]*)/gi,
    /https?:\\\/\\\/[^"'\s>]+\.(?:png|jpg|jpeg|webp)(?:[^"'\s>]*)/gi,
    /https?:\\u002F\\u002F[^"'\s>]+\.(?:png|jpg|jpeg|webp)(?:[^"'\s>]*)/gi,
  ];
  const matches = patterns.flatMap((pattern) => html.match(pattern) || []);

  return [...new Set(
    matches
      .map((match) => toAbsoluteUrl(match, baseUrl))
      .filter(Boolean)
  )];
}

function pickBestImage(baseUrl, ...groups) {
  const candidates = groups
    .flat()
    .map((candidate) => toAbsoluteUrl(candidate, baseUrl))
    .filter(Boolean);

  if (candidates.length === 0) return null;

  return [...new Set(candidates)]
    .sort((left, right) => scoreImageCandidate(right) - scoreImageCandidate(left))[0] || null;
}

function normalizeText(text) {
  return (text || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function uniqueStrings(values) {
  return [...new Set((values || []).map((value) => normalizeText(value)).filter(Boolean))];
}

function getBodyText($) {
  const clonedBody = $("body").clone();
  clonedBody.find("script,style,noscript,svg").remove();
  return normalizeText(clonedBody.text());
}

function collectHeadingSections($) {
  const sections = [];
  const seenHeadings = new Set();
  const headingSelector = "h1, h2, h3, h4, h5, h6";

  $(headingSelector).each((_, heading) => {
    const title = normalizeText($(heading).text());
    if (!title || title.length < 3) return;

    const normalizedTitle = title.toLowerCase();
    if (seenHeadings.has(normalizedTitle)) return;
    seenHeadings.add(normalizedTitle);

    const blocks = [];
    let sibling = $(heading).next();

    while (sibling.length && !sibling.is(headingSelector)) {
      const listItems = sibling
        .find("li")
        .map((__, li) => normalizeText($(li).text()))
        .get()
        .filter(Boolean);

      if (listItems.length > 0) {
        blocks.push(listItems.join("\n"));
      } else {
        const blockText = normalizeText(sibling.text());
        if (blockText) blocks.push(blockText);
      }

      if (blocks.join("\n").length > 4000) break;
      sibling = sibling.next();
    }

    if (blocks.length > 0) {
      sections.push({
        heading: title,
        headingNormalized: normalizedTitle,
        text: normalizeText(blocks.join("\n")),
        blocks: uniqueStrings(
          blocks
            .join("\n")
            .split(/\n+/)
            .map((item) => item.trim())
        ),
      });
    }
  });

  return sections;
}

function findSection(sections, patterns) {
  return sections.find((section) =>
    patterns.some((pattern) => pattern.test(section.headingNormalized))
  ) || null;
}

function parseSaltFromText(text) {
  if (!text) return null;

  const patterns = [
    /salt composition\s*[:\-]?\s*([^.|\n]{3,160})/i,
    /composition\s*[:\-]?\s*([^.|\n]{3,160})/i,
    /generic name\s*[:\-]?\s*([^.|\n]{3,160})/i,
    /contains\s*[:\-]?\s*([^.|\n]{3,160})/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) {
      return normalizeText(match[1]);
    }
  }

  return null;
}

function splitListText(text) {
  if (!text) return [];

  return uniqueStrings(
    text
      .split(/\n|(?:\s{2,})|(?:,\s*(?=[A-Z]))|(?:,\s*(?=[a-z]{3,}\b))/)
      .map((part) => part.replace(/^[-*•]\s*/, "").trim())
      .filter((part) => part && part.length > 2)
  );
}

function parseSideEffectsFromText(text) {
  if (!text) return [];

  const match = text.match(
    /side effects?\s*[:\-]?\s*([^.]{10,250})/i
  );

  if (!match || !match[1]) return [];
  return splitListText(match[1]);
}

function parseFAQFromLDJSON($) {
  const faqs = [];

  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      let data = JSON.parse($(el).html());
      if (data["@graph"]) data = data["@graph"];
      const items = Array.isArray(data) ? data : [data];

      items.forEach((item) => {
        const typeValue = item["@type"];
        const isFaq =
          typeValue === "FAQPage" ||
          (Array.isArray(typeValue) && typeValue.includes("FAQPage"));

        if (!isFaq || !Array.isArray(item.mainEntity)) return;

        item.mainEntity.forEach((entry) => {
          const question = normalizeText(entry.name || entry.question || "");
          const answer = normalizeText(
            entry.acceptedAnswer?.text || entry.acceptedAnswer?.name || entry.text || ""
          );

          if (question && answer) {
            faqs.push({ question, answer });
          }
        });
      });
    } catch {
      // Ignore malformed structured data blocks.
    }
  });

  return faqs;
}

function parseFAQFromSections(sections) {
  const faqSection = findSection(sections, [
    /^faq$/,
    /frequently asked questions?/,
    /questions? and answers?/,
  ]);

  if (!faqSection) return [];

  const lines = faqSection.blocks;
  const faqs = [];

  for (let index = 0; index < lines.length; index += 2) {
    const question = normalizeText(lines[index]);
    const answer = normalizeText(lines[index + 1] || "");
    if (question && answer) {
      faqs.push({ question, answer });
    }
  }

  return faqs;
}

function pickDescription(...candidates) {
  const normalized = candidates
    .flat()
    .map((candidate) => normalizeText(candidate))
    .filter(Boolean)
    .sort((left, right) => right.length - left.length);

  return normalized[0] || null;
}

// ─── Extraction Strategies ──────────────────────────────────────────

/**
 * Strategy 1: Extract from LD+JSON structured data (most reliable).
 */
function extractFromLDJSON($) {
  const result = {};

  $('script[type="application/ld+json"]').each((_, el) => {
    try {
      let data = JSON.parse($(el).html());

      // Handle @graph arrays
      if (data["@graph"]) {
        data = data["@graph"];
      }

      // Normalize to array
      const items = Array.isArray(data) ? data : [data];

      for (const item of items) {
        const typeValue = item["@type"];
        const itemTypes = Array.isArray(typeValue) ? typeValue : [typeValue];
        const isSupportedType = itemTypes.some((type) =>
          ["Product", "Drug", "MedicalEntity", "Substance"].includes(type)
        );

        if (!isSupportedType) {
          continue;
        }

        result.name = result.name || item.name;
        result.brand =
          result.brand ||
          (typeof item.brand === "string"
            ? item.brand
            : item.brand?.name);
        result.image =
          result.image ||
          (Array.isArray(item.image)
            ? item.image[0]
            : item.image);
        result.description =
          result.description || item.description;
        result.salt =
          result.salt ||
          normalizeText(
            item.activeIngredient?.name ||
              item.activeIngredient ||
              item.nonProprietaryName?.name ||
              item.nonProprietaryName ||
              item.drugUnit ||
              ""
          ) ||
          null;

        result.manufacturer =
          result.manufacturer ||
          (typeof item.manufacturer === "string"
            ? item.manufacturer
            : item.manufacturer?.name) ||
          (typeof item.brand === "string"
            ? item.brand
            : item.brand?.name) ||
          null;

        // Rating
        if (item.aggregateRating) {
          result.rating =
            result.rating ||
            parseFloat(item.aggregateRating.ratingValue) ||
            null;
          result.reviewCount =
            result.reviewCount ||
            parseInt(item.aggregateRating.reviewCount, 10) ||
            parseInt(item.aggregateRating.ratingCount, 10) ||
            null;
        }

        // Offers
        const offers = item.offers;
        if (offers) {
          const offer = Array.isArray(offers) ? offers[0] : offers;
          result.price = result.price || parseFloat(offer.price) || null;
          result.currency = result.currency || offer.priceCurrency || null;
          result.availability =
            result.availability ||
            (offer.availability
              ? offer.availability.replace("https://schema.org/", "").replace("http://schema.org/", "")
              : null);
        }
      }
    } catch {
      // Ignore malformed JSON-LD
    }
  });

  return Object.keys(result).length > 0 ? result : null;
}

/**
 * Strategy 2: Extract from meta tags.
 */
function extractFromMeta($) {
  const result = {};

  const meta = (name) =>
    $(`meta[property="${name}"], meta[name="${name}"]`).attr("content") || null;

  result.name = meta("og:title") || meta("twitter:title");
  result.image = meta("og:image") || meta("twitter:image");
  result.description = meta("og:description") || meta("description");
  result.price =
    parsePrice(meta("product:price:amount") || meta("og:price:amount"));
  result.currency =
    meta("product:price:currency") || meta("og:price:currency");
  result.availability = meta("product:availability");
  result.brand = meta("product:brand");
  result.manufacturer = meta("product:manufacturer") || meta("manufacturer") || null;

  // Remove null values
  for (const key of Object.keys(result)) {
    if (result[key] === null || result[key] === undefined) {
      delete result[key];
    }
  }

  return Object.keys(result).length > 0 ? result : null;
}

/**
 * Strategy 3: Extract using CSS selectors (broadest coverage).
 */
function extractFromSelectors($) {
  const nameText = getFirstMatch($, SELECTORS.name);
  const priceText = getFirstMatch($, SELECTORS.price);
  const mrpText = getFirstMatch($, SELECTORS.mrp);
  const discountText = getFirstMatch($, SELECTORS.discount);
  const quantityText = getFirstMatch($, SELECTORS.quantity);
  const brandText = getFirstMatch($, SELECTORS.brand);
  const manufacturerText = getFirstMatch($, SELECTORS.manufacturer);
  const ratingText = getFirstMatch($, SELECTORS.rating);
  const imageUrl = getFirstImageSrc($, SELECTORS.image);
  const availabilityText = getFirstMatch($, SELECTORS.availability);
  const saltText = getFirstMatch($, SELECTORS.salt);
  const descriptionText = getFirstMatch($, SELECTORS.description);

  const result = {};

  if (nameText) result.name = nameText;
  if (priceText) {
    result.price = parsePrice(priceText);
    result.priceRaw = priceText;
    if (!result.currency) {
      result.currency = parseCurrency(priceText);
    }
  }
  if (mrpText) {
    result.mrp = parsePrice(mrpText);
    result.mrpRaw = mrpText;
  }
  if (discountText) result.discount = discountText;
  if (quantityText) result.quantity = quantityText;
  if (brandText) result.brand = brandText;
  if (manufacturerText) result.manufacturer = manufacturerText;
  if (ratingText) result.rating = parseRating(ratingText);
  if (imageUrl) result.image = imageUrl;
  if (availabilityText) result.availability = availabilityText;
  if (saltText) result.salt = saltText;
  if (descriptionText) result.description = descriptionText;

  return Object.keys(result).length > 0 ? result : null;
}

// ─── Main Product Detail Scraper ────────────────────────────────────

/**
 * Fetch HTML from a URL using Axios.
 */
async function fetchPageHTML(url, timeout = 15000) {
  const response = await axios.get(url, {
    timeout,
    headers: {
      "User-Agent": UA,
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

/**
 * Fetch HTML using Puppeteer (for JS-rendered pages).
 */
async function fetchPageHTMLWithBrowser(url, timeout = 30000) {
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
    await page.setViewport({ width: 1440, height: 900 });
    await page.setUserAgent(UA);

    await page.goto(url, {
      waitUntil: "networkidle2",
      timeout,
    });

    // Wait for the page content to render (React/Next.js apps)
    try {
      await page.waitForSelector('h1, [class*="product"], [class*="Product"]', {
        timeout: 5000,
      });
    } catch {
      // Selector not found, proceed anyway
    }

    // Scroll down to trigger lazy-loaded content
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
        setTimeout(() => { clearInterval(timer); resolve(); }, 5000);
      });
    });

    // Wait for final renders
    await new Promise((r) => setTimeout(r, 2000));

    return await page.content();
  } finally {
    if (browser) await browser.close();
  }
}

/**
 * Scrape product details from a single PDP URL.
 *
 * @param {string} url - The PDP URL to scrape.
 * @param {object} opts
 * @param {number} opts.timeout - Request timeout in ms.
 * @param {string} opts.mode - "auto" | "fast" | "browser"
 * @returns {Promise<object>} Product details object.
 */
async function scrapeProductDetail(url, opts = {}) {
  const { timeout = 15000, mode = "auto" } = opts;

  /**
   * Helper: given loaded cheerio $, extract and merge product data.
   */
  function extractProduct($, html) {
    const ldJson = extractFromLDJSON($) || {};
    const meta = extractFromMeta($) || {};
    const selectors = extractFromSelectors($) || {};
    const sections = collectHeadingSections($);
    const bodyText = getBodyText($);
    const htmlImageCandidates = extractImageCandidatesFromHtml(html, url);

    const descriptionSection = findSection(sections, [
      /description/,
      /overview/,
      /about/,
      /introduction/,
      /^uses$/,
      /product details?/,
    ]);

    const saltSection = findSection(sections, [
      /salt composition/,
      /^composition$/,
      /generic name/,
      /ingredients?/,
      /active ingredients?/,
    ]);

    const dosageSection = findSection(sections, [
      /dosage/,
      /directions? for use/,
      /how to use/,
      /recommended dose/,
      /pack size/,
    ]);

    const sideEffectsSection = findSection(sections, [
      /side effects?/,
      /adverse effects?/,
      /undesirable effects?/,
    ]);

    const faq = uniqueStrings(
      [
        ...parseFAQFromLDJSON($).map((item) => JSON.stringify(item)),
        ...parseFAQFromSections(sections).map((item) => JSON.stringify(item)),
      ]
    ).map((item) => JSON.parse(item));

    const description = pickDescription(
      ldJson.description,
      meta.description,
      selectors.description,
      descriptionSection?.text
    );
    const descriptionPrice = parsePriceNearCurrency(description || "");
    const bodyPrice = parsePriceNearCurrency(bodyText || "");

    const salt = pickDescription(
      ldJson.salt,
      selectors.salt,
      parseSaltFromText(saltSection?.text),
      parseSaltFromText(bodyText)
    );

    const dosage =
      extractDosage(ldJson.name || "") ||
      extractDosage(meta.name || "") ||
      extractDosage(selectors.name || "") ||
      extractDosage(salt || "") ||
      extractDosage(selectors.quantity || "") ||
      extractDosage(dosageSection?.text || "") ||
      extractDosage(description || "");

    const sideEffects = uniqueStrings([
      ...splitListText(sideEffectsSection?.text || ""),
      ...parseSideEffectsFromText(bodyText),
    ]);

    return {
      ldJson,
      meta,
      selectors,
      sections,
      merged: {
        url,
        platform: detectPlatform(url),
        name: ldJson.name || meta.name || selectors.name || null,
        brand: ldJson.brand || meta.brand || selectors.brand || null,
        manufacturer:
          ldJson.manufacturer ||
          meta.manufacturer ||
          selectors.manufacturer ||
          ldJson.brand ||
          meta.brand ||
          selectors.brand ||
          null,
        price: ldJson.price || meta.price || selectors.price || descriptionPrice || bodyPrice || null,
        priceRaw: selectors.priceRaw || null,
        mrp: selectors.mrp || null,
        mrpRaw: selectors.mrpRaw || null,
        discount: selectors.discount || null,
        currency: ldJson.currency || meta.currency || selectors.currency || null,
        quantity: selectors.quantity || null,
        availability: ldJson.availability || meta.availability || selectors.availability || null,
        rating: ldJson.rating || selectors.rating || null,
        reviewCount: ldJson.reviewCount || null,
        image: pickBestImage(url, [ldJson.image, meta.image, selectors.image], htmlImageCandidates),
        description,
        salt,
        dosage,
        sideEffects,
        faq,
        sourceType:
          ldJson.price || meta.price || selectors.price || descriptionPrice || bodyPrice
            ? "product"
            : "information",
      },
    };
  }

  /**
   * Check if extraction got meaningful data (at least name + price).
   */
  function hasGoodData(product) {
    return (
      !!product.name &&
      !!(
        product.price ||
        product.description ||
        product.salt ||
        (Array.isArray(product.sideEffects) && product.sideEffects.length > 0)
      )
    );
  }

  let product = null;
  let usedMode = "fast";

  // ─── FAST mode: try Axios first ────────────────────────────────
  if (mode === "fast" || mode === "auto") {
    try {
      const html = await fetchPageHTML(url, timeout);
      usedMode = "fast";
      const $ = cheerio.load(html);
      const result = extractProduct($, html);
      product = result.merged;
      product.mode = usedMode;

      // If we got good data, return it
      if (hasGoodData(product) || mode === "fast") {
        // Compute discount
        if (product.price && product.mrp && !product.discount && product.mrp > product.price) {
          const pct = Math.round(((product.mrp - product.price) / product.mrp) * 100);
          product.discount = `${pct}% off`;
        }
        return product;
      }

      // No good data — fall through to browser
      console.error(`  ⚠️  Fast fetch got no meaningful data for ${url}. Trying browser...`);
    } catch (err) {
      if (mode === "fast") {
        return { url, error: `Failed to fetch: ${err.message}`, mode: "fast" };
      }
      console.error(`  ⚠️  Fast fetch failed for ${url} (${err.message}). Trying browser...`);
    }
  }

  // ─── BROWSER mode: use Puppeteer ───────────────────────────────
  try {
    const html = await fetchPageHTMLWithBrowser(url, timeout + 15000);
    usedMode = "browser";
    const $ = cheerio.load(html);
    const result = extractProduct($, html);
    product = result.merged;
    product.mode = usedMode;

    // Compute discount
    if (product.price && product.mrp && !product.discount && product.mrp > product.price) {
      const pct = Math.round(((product.mrp - product.price) / product.mrp) * 100);
      product.discount = `${pct}% off`;
    }

    return product;
  } catch (err) {
    return {
      url,
      error: `Failed to fetch with browser: ${err.message}`,
      mode: "browser",
    };
  }
}

/**
 * Scrape product details from multiple PDP URLs with concurrency control.
 *
 * @param {string[]} urls - Array of PDP URLs to scrape.
 * @param {object} opts
 * @param {number} opts.concurrency - Max concurrent requests (default 5).
 * @param {number} opts.timeout - Timeout per request in ms.
 * @param {string} opts.mode - "auto" | "fast" | "browser"
 * @param {number} opts.delayMs - Delay between batches in ms (default 500).
 * @returns {Promise<object[]>} Array of product detail objects.
 */
async function scrapeProductDetails(urls, opts = {}) {
  const {
    concurrency = 5,
    timeout = 15000,
    mode = "auto",
    delayMs = 500,
  } = opts;

  const results = [];

  // Process in batches
  for (let i = 0; i < urls.length; i += concurrency) {
    const batch = urls.slice(i, i + concurrency);
    const batchNum = Math.floor(i / concurrency) + 1;
    const totalBatches = Math.ceil(urls.length / concurrency);

    console.log(
      `  📦 Batch ${batchNum}/${totalBatches}: Scraping ${batch.length} product(s)...`
    );

    const batchResults = await Promise.allSettled(
      batch.map((url) => scrapeProductDetail(url, { timeout, mode }))
    );

    for (const r of batchResults) {
      if (r.status === "fulfilled") {
        results.push(r.value);
      } else {
        results.push({ url: "unknown", error: r.reason?.message || "Unknown error" });
      }
    }

    // Delay between batches to be polite
    if (i + concurrency < urls.length) {
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }

  return results;
}

module.exports = {
  scrapeProductDetail,
  scrapeProductDetails,
  extractFromLDJSON,
  extractFromMeta,
  extractFromSelectors,
};
