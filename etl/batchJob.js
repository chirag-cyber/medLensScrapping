const { discoverMedicineUrls } = require("./extract/discovery");
const { detectPlatform } = require("./extract/platforms");
const { runETL } = require("./pipeline");
const { transformRecord } = require("./transform/transformer");
const { scrapeProductDetail } = require("../productScraper");

async function scrapeDiscoveredSources(sources, options = {}) {
  const {
    concurrency = 4,
    timeout = 15000,
    mode = "auto",
    delayMs = 400,
    queryHint = null,
  } = options;

  const rawRecords = [];
  const failures = [];
  const transformedPreview = [];

  for (let index = 0; index < sources.length; index += concurrency) {
    const batch = sources.slice(index, index + concurrency);
    const batchResults = await Promise.allSettled(
      batch.map((source) => scrapeProductDetail(source.url, { timeout, mode }))
    );

    batchResults.forEach((result, batchIndex) => {
      const source = batch[batchIndex];

      if (result.status !== "fulfilled" || result.value?.error) {
        failures.push({
          url: source.url,
          platform: source.platform,
          error: result.status === "fulfilled" ? result.value.error : result.reason?.message,
        });
        return;
      }

      const rawRecord = {
        ...result.value,
        url: source.url,
        platform: source.platform || detectPlatform(source.url),
        sourceType: result.value.sourceType || "product",
        discoveryMethod: source.discoveryMethod,
        queryHint: source.queryHint || queryHint || null,
      };

      rawRecords.push(rawRecord);
      transformedPreview.push(transformRecord(rawRecord));
    });

    if (index + concurrency < sources.length) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  return {
    rawRecords,
    failures,
    transformedPreview,
  };
}

async function ingestMedicineQuery(query, options = {}) {
  const sources = await discoverMedicineUrls(query, options);
  const scrapeSummary = await scrapeDiscoveredSources(sources, {
    ...options,
    queryHint: query,
  });
  const etlSummary = await runETL(scrapeSummary.rawRecords, {
    batchSize: options.dbBatchSize || 25,
  });

  const fullyDetailedSources = scrapeSummary.transformedPreview.filter(
    (record) => record.missing_detail_fields.length === 0
  ).length;

  return {
    query,
    discoveredSources: sources.length,
    scrapedSources: scrapeSummary.rawRecords.length,
    failedSources: scrapeSummary.failures.length,
    fullyDetailedSources,
    strictRejectedMedicines: etlSummary.strictRejectedCount || 0,
    failures: scrapeSummary.failures,
    etl: etlSummary,
    sources,
  };
}

async function runBatchIngestion(queries, options = {}) {
  const cleanedQueries = (queries || []).map((query) => String(query).trim()).filter(Boolean);
  const queryBatchSize = options.queryBatchSize || 2;
  const results = [];

  for (let index = 0; index < cleanedQueries.length; index += queryBatchSize) {
    const batch = cleanedQueries.slice(index, index + queryBatchSize);
    const batchResults = await Promise.allSettled(
      batch.map((query) => ingestMedicineQuery(query, options))
    );

    batchResults.forEach((result, batchIndex) => {
      const query = batch[batchIndex];
      if (result.status === "fulfilled") {
        results.push(result.value);
      } else {
        results.push({
          query,
          error: result.reason?.message || "Batch ingestion failed",
        });
      }
    });
  }

  return {
    totalQueries: cleanedQueries.length,
    successfulQueries: results.filter((result) => !result.error).length,
    failedQueries: results.filter((result) => result.error).length,
    results,
  };
}

module.exports = {
  ingestMedicineQuery,
  runBatchIngestion,
  scrapeDiscoveredSources,
};
