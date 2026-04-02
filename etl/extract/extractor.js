/**
 * ETL Extractor
 * Reads from an array of scraped objects and pre-validates them before transformation.
 */

function extractData(rawScrapeArray) {
  if (!Array.isArray(rawScrapeArray)) {
    throw new Error("Extractor requires an array of raw product json objects.");
  }

  // Pre-filter empty or fundamentally invalid data before passing to transform
  const validInputs = rawScrapeArray.filter(
    (item) => item.name && typeof item.price === "number" && item.url
  );

  console.log(`[ETL EXTRACT] Extracted ${validInputs.length} valid payloads out of ${rawScrapeArray.length} raw payloads.`);
  return validInputs;
}

module.exports = {
  extractData
};
