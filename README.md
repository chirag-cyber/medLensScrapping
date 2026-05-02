# 💊 MediSaathi ETL Pipeline

A production-grade **medicine data extraction, transformation, and loading pipeline** for the Indian pharmacy market. Scrapes medicine data from multiple platforms (1mg, PharmEasy, Netmeds, Apollo), deduplicates records using intelligent fuzzy matching, and enriches them with AI-powered metadata.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    MediSaathi ETL Pipeline                    │
│                                                              │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │ STAGE 1  │──▶│   STAGE 2    │──▶│      STAGE 3         │ │
│  │ Scrape   │   │  Interlink   │   │  Enrich              │ │
│  │          │   │              │   │  ┌──────────────────┐ │ │
│  │ • 1mg    │   │ • Exact key  │   │  │ 3a. Targeted     │ │ │
│  │ • Apollo │   │ • Fuzzy name │   │  │ (missing prices) │ │ │
│  │ • Netmeds│   │ • Int dosage │   │  ├──────────────────┤ │ │
│  │ • Pharm  │   │              │   │  │ 3b. LLM Enrich   │ │ │
│  │   Easy   │   │              │   │  │ (AI metadata)    │ │ │
│  └──────────┘   └──────────────┘   │  └──────────────────┘ │ │
│                                     └──────────────────────┘ │
│                           │                                  │
│                     ┌─────▼─────┐                            │
│                     │  MongoDB  │                            │
│                     │ (MEDSAVE) │                            │
│                     └───────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

---

## 📦 Modules

### Stage 1 — Scraping (`orchestrator.js`)

| File | Purpose |
|---|---|
| `orchestrator.js` | Master crawler — iterates over salt/alphabet queries and feeds them into scrapers |
| `scraper.js` | Discovers PDP (Product Detail Page) links from pharmacy websites |
| `productScraper.js` | Extracts structured data (name, price, salt, dosage) from individual product pages |
| `etl/pipeline.js` | Transforms raw scraped data and upserts into MongoDB |
| `etl/batchJob.js` | Batch ingestion engine — processes multiple queries with rate limiting |

**What it does:**
- Searches each platform (1mg, PharmEasy, Netmeds, Apollo) for medicines by salt name or alphabet
- Extracts: name, price, MRP, salt composition, dosage, manufacturer, image, description
- Normalizes and upserts into MongoDB with deduplication at the platform level
- Fault-tolerant: saves state to `.crawler-state.json` for crash recovery

---

### Stage 2 — Interlinking (`interlink-medicines.js`)

**What it does:**
Finds and merges duplicate medicine records that scrapers stored under slightly different names.

**Three-Pass Deduplication Strategy:**

| Pass | Method | Example Match |
|---|---|---|
| **Pass 1** | Exact `canonical_key` match | Both records have `name:dolo 650mg\|dose:650mg` |
| **Pass 2** | Fuzzy name + dosage matching | `"leemol 650mg tablets"` ↔ `"leemol-650 tablet"` |
| **Pass 3** | Integer dosage + salt fallback | `"nitrolong 2.6mg"` ↔ `"nitrolong 2. cr"` (base int: 2) |

**Safety Features:**
- MongoDB transactions for atomic merges
- Backs up deleted records to `deleted_medicines` collection + JSON files
- Marks master records with `is_canonical: true`
- Deduplicates prices per platform before merging
- Strips manufacturer names, formulation words (tablet, capsule, CR, SR, XR), and dosage units from comparison

---

### Stage 3a — Targeted Enrichment (`targeted-enrichment.js`)

**What it does:**
Scans the database for medicines that are missing prices from priority platforms and launches targeted scrapers to find them.

**Example:** If "Dolo 650mg" only has a price from 1mg but not PharmEasy/Netmeds/Apollo, this module will search those platforms specifically for that medicine.

---

### Stage 3b — LLM Enrichment (`enrich-medicines.js`)

**What it does:**
Uses AI (Groq API) to populate missing metadata:
- Descriptions
- Side effects
- FAQs
- Salt/composition verification

Only runs on records where `llm_enriched !== true`.

---

## 🚀 Quick Start

### Prerequisites
- Node.js 18+ 
- MongoDB (Atlas or local)
- Chrome/Chromium (for Puppeteer-based scraping)

### Installation
```bash
git clone https://github.com/chirag-cyber/medLensScrapping.git
cd medLensScrapping
npm install
```

### Environment Variables
Create a `.env` file:
```env
MONGO_URL=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxx
```

### Run the Server
```bash
npm start
# Server starts at http://localhost:8000
```

---

## ⏱️ Pipeline Scheduling Guide

### Recommended Cron Schedule

| Stage | Command | Duration | Frequency | Why |
|---|---|---|---|---|
| **Scrape** | `npm run crawl:resume` | 30–60 min | Every 6 hours | Platform data changes throughout the day |
| **Interlink** | `npm run interlink` | 2–5 min | After every scrape | Clean duplicates before enrichment |
| **Targeted Enrich** | `npm run targeted-enrich` | 15–30 min | Every 12 hours | Fill missing platform prices |
| **LLM Enrich** | `npm run enrich` | 10–20 min | Once daily | AI metadata is stable, no need to refresh often |
| **Full Pipeline** | `npm run pipeline` | 60–120 min | Once daily (night) | End-to-end refresh |

### Example Crontab
```bash
# ┌───── minute (0-59)
# │ ┌───── hour (0-23)
# │ │ ┌───── day of month (1-31)
# │ │ │ ┌───── month (1-12)
# │ │ │ │ ┌───── day of week (0-6, Sun=0)

# Scrape every 6 hours
0 */6 * * *   cd /path/to/medLensScrapping && npm run crawl:resume >> logs/scrape.log 2>&1

# Interlink 15 min after each scrape
15 */6 * * *  cd /path/to/medLensScrapping && npm run interlink >> logs/interlink.log 2>&1

# Targeted enrichment twice a day
0 8,20 * * *  cd /path/to/medLensScrapping && npm run targeted-enrich >> logs/targeted.log 2>&1

# LLM enrichment once at midnight
0 0 * * *     cd /path/to/medLensScrapping && npm run enrich >> logs/enrich.log 2>&1

# OR: Full pipeline once at 2 AM
0 2 * * *     cd /path/to/medLensScrapping && npm run pipeline >> logs/pipeline.log 2>&1
```

### Using HTTP Cron Triggers (for cloud/serverless)

If your server is running, you can trigger batches via HTTP — perfect for services like **cron-job.org**, **Uptime Robot**, or **Railway cron**:

| Endpoint | Method | Description |
|---|---|---|
| `/cron/scrape` | GET | Start the orchestrator (scraping) |
| `/cron/interlink` | GET | Run deduplication |
| `/cron/targeted-enrichment?limit=50` | GET | Enrich missing platform prices |
| `/cron/llm-enrich` | GET | Run AI metadata enrichment |
| `/cron/full-pipeline` | GET | Run all 4 stages sequentially |
| `/cron/status` | GET | Check which jobs are currently running |

**Safety:** Each job has a built-in guard that prevents double-triggering. If a job is already running, the endpoint returns `409 Conflict`.

**Query Parameters:**

| Endpoint | Param | Default | Description |
|---|---|---|---|
| `/cron/scrape` | `reset=true` | `false` | Clear state and start fresh |
| `/cron/scrape` | `batchSize=5` | `3` | Queries per batch |
| `/cron/interlink` | `dryRun=true` | `false` | Preview without making changes |
| `/cron/targeted-enrichment` | `limit=100` | `50` | Max medicines to process |
| `/cron/targeted-enrichment` | `dryRun=true` | `false` | Preview mode |

---

## 🛠️ CLI Commands

| Command | Description |
|---|---|
| `npm start` | Start the HTTP server (port 8000) |
| `npm run crawl` | Run the orchestrator (full scrape) |
| `npm run crawl:resume` | Resume scraping from last checkpoint |
| `npm run crawl:salts` | Scrape using clinical salt list |
| `npm run crawl:alphabet` | Scrape using aa-zz letter combinations |
| `npm run crawl:dry-run` | Preview scrape queries without executing |
| `npm run interlink` | Run deduplication (live merge) |
| `npm run interlink:dry-run` | Preview deduplication without merging |
| `npm run targeted-enrich` | Fill missing platform prices (50 medicines) |
| `npm run enrich` | Run LLM enrichment |
| `npm run pipeline` | Run full pipeline: Scrape → Interlink → Enrich |

---

## 📊 Database Schema

### `medicines` Collection
| Field | Type | Description |
|---|---|---|
| `name` | String | Original product name |
| `normalized_name` | String | Cleaned name for matching |
| `salt` | String | Active pharmaceutical ingredient |
| `dosage` | String | Dosage strength (e.g., "650mg") |
| `manufacturer` | String | Drug manufacturer |
| `description` | String | Product description |
| `image_url` | String | Product image URL |
| `source_platforms` | [String] | Platforms this medicine was found on |
| `side_effects` | [String] | Known side effects |
| `faq` | [{question, answer}] | FAQs about the medicine |
| `canonical_key` | String | Dedup key: `name:<norm>|dose:<dose>` |
| `is_canonical` | Boolean | True if this is the master record |
| `llm_enriched` | Boolean | True if AI has enriched this record |
| `last_enriched_at` | Date | Timestamp of last targeted enrichment |

### `prices` Collection
| Field | Type | Description |
|---|---|---|
| `medicine_id` | ObjectId | Reference to medicine |
| `platform` | String | Platform name (1mg, apollo, etc.) |
| `price` | Number | Current selling price |
| `mrp` | Number | Maximum retail price |
| `discount` | Number | Discount percentage |
| `url` | String | Product page URL |

### `deleted_medicines` Collection
Backup of all records merged during interlinking. Contains the original document plus:
- `_original_id` — The original `_id` before deletion
- `_merged_into` — The master record's `_id`
- `_merged_at` — When the merge happened

---

## 🔒 Safety & Recovery

- **Atomic Merges:** All interlinking operations use MongoDB transactions
- **Full Backups:** Every merge creates entries in both the `deleted_medicines` collection and a timestamped JSON file in `backups/`
- **Crash Recovery:** The orchestrator saves progress to `.crawler-state.json` and resumes from the exact point of failure
- **Idempotent:** Running any stage multiple times produces the same result
- **Double-Trigger Protection:** HTTP cron endpoints reject requests if a job is already running

---

## 📁 Project Structure

```
medLensScrapping/
├── server.js                    # Express HTTP server with all API + cron routes
├── orchestrator.js              # Master scraping orchestrator
├── scraper.js                   # PDP link discovery engine
├── productScraper.js            # Product detail extractor (Puppeteer)
├── interlink-medicines.js       # 3-pass deduplication engine
├── targeted-enrichment.js       # Missing platform price filler
├── enrich-medicines.js          # LLM metadata enrichment
├── etl/
│   ├── pipeline.js              # Transform + Load pipeline
│   ├── batchJob.js              # Batch ingestion engine
│   ├── search.js                # Medicine search API
│   ├── load/
│   │   └── db.js                # MongoDB connection manager
│   └── enrich/
│       └── enricher.js          # LLM enrichment logic (Groq)
├── salts.json                   # Clinical salt list for discovery
├── backups/                     # JSON backups of merged records
├── .crawler-state.json          # Orchestrator resume state
├── .env                         # Environment variables
└── package.json
```

---

## 📄 License

MIT
