const sqlite3 = require("sqlite3");
const { open } = require("sqlite");
const path = require("path");

// Function to initialize and open the DB
async function getDbConnection() {
  const db = await open({
    filename: path.join(__dirname, "scraper.db"),
    driver: sqlite3.Database,
  });

  // Create table if it doesn't exist
  await db.exec(`
    CREATE TABLE IF NOT EXISTS scrapes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_url TEXT NOT NULL,
      mode TEXT,
      pdp_links_found INTEGER,
      products_scraped INTEGER,
      scraped_at TEXT NOT NULL,
      result_json TEXT NOT NULL
    )
  `);

  return db;
}

/**
 * Save scrape results to the database
 * @param {Object} resultObj - The full JSON result object.
 */
async function saveScrapeResult(resultObj) {
  try {
    const db = await getDbConnection();
    await db.run(
      `INSERT INTO scrapes (source_url, mode, pdp_links_found, products_scraped, scraped_at, result_json)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [
        resultObj.source || "unknown",
        resultObj.mode || "unknown",
        resultObj.pdpLinksFound || 0,
        resultObj.productsScraped || 0,
        resultObj.scrapedAt || new Date().toISOString(),
        JSON.stringify(resultObj),
      ]
    );
    console.log(`[DB] Successfully saved scrape result for: ${resultObj.source}`);
  } catch (err) {
    console.error("[DB ERROR] Failed to save scrape result:", err);
  }
}

/**
 * Get all previously saved scrapes (without the full result JSON by default, or all).
 * @param {boolean} includeJson - Whether to include the large JSON blob in the response.
 */
async function getSavedScrapes(includeJson = false, limit = 50) {
  try {
    const db = await getDbConnection();
    const columns = includeJson
      ? "*"
      : "id, source_url, mode, pdp_links_found, products_scraped, scraped_at";
      
    const rows = await db.all(`SELECT ${columns} FROM scrapes ORDER BY id DESC LIMIT ?`, [limit]);
    
    // Parse json if included
    if (includeJson) {
      return rows.map(r => ({ ...r, result_json: JSON.parse(r.result_json) }));
    }
    
    return rows;
  } catch (err) {
    console.error("[DB ERROR] Failed to retrieve scrapes:", err);
    throw err;
  }
}

/**
 * Get a single specific scrape by ID.
 */
async function getSavedScrapeById(id) {
  try {
    const db = await getDbConnection();
    const row = await db.get(`SELECT * FROM scrapes WHERE id = ?`, [id]);
    
    if (row) {
      row.result_json = JSON.parse(row.result_json);
    }
    return row;
  } catch (err) {
    console.error("[DB ERROR] Failed to retrieve scrape by ID:", err);
    throw err;
  }
}

/**
 * Get the most recent scrape by Source URL.
 */
async function getSavedScrapeByUrl(url) {
  try {
    const db = await getDbConnection();
    // Order by ID DESC to get the most recent scrape for this URL
    const row = await db.get(`SELECT * FROM scrapes WHERE source_url = ? ORDER BY id DESC LIMIT 1`, [url]);
    
    if (row) {
      row.result_json = JSON.parse(row.result_json);
    }
    return row;
  } catch (err) {
    console.error("[DB ERROR] Failed to retrieve scrape by URL:", err);
    throw err;
  }
}

module.exports = {
  saveScrapeResult,
  getSavedScrapes,
  getSavedScrapeById,
  getSavedScrapeByUrl,
};
