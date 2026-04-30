# Project Status And Implementation Summary

## Overview

This repository started as a generic PDP link scraper.

It has now been extended into a medicine-focused scraping and ETL pipeline that:

- discovers medicine/product pages across multiple pharmacy platforms
- scrapes structured medicine details from those pages
- transforms the raw scrape output into canonical medicine records
- bulk-upserts clean records into MongoDB
- keeps per-platform price rows linked to canonical medicines
- supports batch-style ingestion for one or many medicine queries
- stores image URLs so the app can show a medicine thumbnail along with name and salt

The current direction of the repo is:

- discover as many relevant medicine pages as possible
- reject incomplete records before MongoDB insertion
- keep the ingestion flow stable with batching and deduplication
- make the data usable directly by an application layer

---

## What Changed

### 1. Multi-platform medicine discovery was added

The repo no longer depends only on scraping a single category/search page.

Current discovery flow:

- platform-native search page discovery
- platform-specific discovery logic where needed
- web-search fallback for more coverage
- deduplication across all found URLs

Main files:

- `etl/extract/platforms.js`
- `etl/extract/discovery.js`

Key platform support now included:

- `1mg`
- `pharmeasy`
- `netmeds`
- `apollo`
- `truemeds` via generic platform rules
- `medplus` via generic platform rules

Special discovery logic added:

- `PharmEasy` typeahead API search + HTML URL extraction
- `Netmeds` product listing state parsing from the search page

Important behavior:

- diagnostics, lab tests, articles, blogs, profile pages, and similar non-medicine URLs are filtered out
- per-platform discovery limit is now unlimited by default
- `perPlatformLimit=all`, `0`, `none`, or omitting the value means no cap

---

### 2. Product detail scraping was upgraded

The original scraper was expanded from basic link detection into medicine detail extraction.

Main file:

- `productScraper.js`

Fields now extracted when available:

- `name`
- `price`
- `mrp`
- `discount`
- `currency`
- `quantity`
- `availability`
- `rating`
- `image`
- `description`
- `salt`
- `dosage`
- `sideEffects`
- `faq`
- `sourceType`

Extraction strategy now combines:

- JSON-LD structured data
- meta tags
- CSS selectors
- section/heading text parsing
- body text fallback parsing

Image extraction was also improved:

- absolute image URLs are normalized
- product images are preferred over icons, logos, blog images, and decorative assets
- image links now work on tested examples from `PharmEasy`, `1mg`, and `Netmeds`

---

### 3. A full ETL pipeline was added

The repo now has a structured ETL layout:

- `etl/extract`
- `etl/transform`
- `etl/load`

Main orchestrator:

- `etl/pipeline.js`

ETL flow:

1. Extract and pre-validate raw scrape records
2. Transform and normalize data
3. Merge duplicates by canonical medicine key
4. Reject incomplete medicine groups
5. Bulk upsert canonical medicine documents
6. Bulk upsert per-platform price documents

This is no longer one-record-at-a-time persistence. It is batch-oriented.

---

## Current ETL Architecture

### Extract layer

Files:

- `etl/extract/extractor.js`
- `etl/extract/platforms.js`
- `etl/extract/discovery.js`

Responsibilities:

- validate raw scraped objects
- define platform URL rules
- discover medicine URLs by query
- filter obviously bad or irrelevant links
- deduplicate discovered URLs

### Transform layer

File:

- `etl/transform/transformer.js`

Responsibilities:

- normalize medicine name
- normalize salt/composition
- extract dosage consistently
- normalize FAQ arrays
- normalize side effects lists
- generate canonical keys
- compute missing mandatory detail fields
- normalize `image_url`

### Load layer

Files:

- `etl/load/models.js`
- `etl/load/db.js`
- `etl/load/upsert.js`

Responsibilities:

- define MongoDB schemas
- create and migrate indexes safely
- merge records by canonical medicine identity
- bulk upsert medicine rows
- bulk upsert platform price rows

### Batch execution layer

Files:

- `etl/batchJob.js`
- `batch-job.js`
- `test-actual.js`

Responsibilities:

- run the full discovery + scrape + ETL pipeline for one query
- run the same flow for multiple queries in batches
- provide a CLI-friendly execution path

---

## Canonical Medicine Strategy

The pipeline does not blindly store one row per scraped page.

Instead, it tries to group pages into a canonical medicine identity using:

- normalized salt + dosage when available
- normalized name when salt-based grouping is not possible

Canonical key generation is handled in:

- `etl/transform/transformer.js`

Examples of canonical key patterns:

- `salt:<normalized_salt>|dose:<dosage>`
- `name:<normalized_name>`
- `salt:<normalized_salt>`

This allows the system to merge multiple platform pages into one medicine record.

---

## MongoDB Data Model

### Medicine collection

Defined in:

- `etl/load/models.js`

Important fields currently stored:

- `name`
- `canonical_key`
- `normalized_name`
- `salt`
- `normalized_salt`
- `dosage`
- `image_url`
- `description`
- `side_effects`
- `faq`
- `source_platforms`
- `last_ingested_at`
- timestamps

### Price collection

Also defined in:

- `etl/load/models.js`

Important fields:

- `medicine_id`
- `platform`
- `price`
- `url`
- `source_type`
- timestamps

This split lets the app use:

- one canonical medicine record
- many current platform prices linked to that medicine

---

## Indexing And MongoDB Fixes

Several MongoDB stability issues were fixed while building the ETL.

Files involved:

- `etl/load/models.js`
- `etl/load/db.js`

Fixes completed:

- removed duplicate schema/index declaration problems
- replaced invalid partial-index expressions with Mongo-compatible ones
- cleaned legacy `canonical_key: null` and `canonical_key: ""` records before index creation
- replaced conflicting legacy text indexes
- named the current text index consistently as `medicine_text_search`
- added safe bootstrap logic to migrate older index layouts before new runs

Important current indexes:

- unique partial index on `canonical_key`
- text index on `normalized_name`, `salt`, and `description`
- compound index on `normalized_salt` + `dosage`
- compound unique index on `medicine_id` + `platform` for prices

---

## Strict Completeness Rules

The user requirement was that certain fields are mandatory.

The pipeline now enforces strict completeness before insertion.

Current mandatory fields for product-type records:

- `name`
- `price`
- `salt`
- `description`
- `dosage`
- `sideEffects`

`faq` is treated as optional.

This logic is implemented in:

- `etl/transform/transformer.js`
- `etl/load/upsert.js`

What happens now:

- records are transformed first
- records are merged by canonical key
- the merged medicine candidate is checked for completeness
- incomplete medicines are rejected from insertion
- incomplete entries are returned in ETL summaries for visibility

This means the DB is intentionally stricter than the raw scraper output.

---

## Batch And Smooth Processing

The repo was reworked so ingestion happens in controlled batches instead of fragile one-by-one inserts.

Batch behavior now exists in two places:

- scrape batching
- DB upsert batching

Scrape batching:

- controlled via concurrency
- implemented in `etl/batchJob.js`

DB batching:

- controlled via `batchSize`
- implemented in `etl/pipeline.js` and `etl/load/upsert.js`

Benefits:

- smoother memory usage
- fewer Mongo writes
- easier handling of duplicate medicines
- cleaner summary reporting

---

## Scripts Added Or Updated

From `package.json`:

- `npm start`
- `npm run scrape`
- `npm run batch:ingest`
- `npm run test:actual`

Current usage:

```bash
npm run test:actual -- "vitamin c"
```

or

```bash
node test-actual.js "vitamin c"
```

Batch mode:

```bash
node batch-job.js "paracetamol 650,azithromycin 500"
```

Optional uncapped discovery example:

```bash
node batch-job.js "vitamin c" --per-platform-limit all
```

Note:

- the current default is already uncapped per platform unless a positive limit is explicitly provided

---

## API Endpoints Added Or Extended

Main API file:

- `server.js`

Current useful endpoints:

- `GET /scrape`
- `GET /scrape-details`
- `GET /product-detail`
- `GET /ingest-medicine`
- `POST /ingest-batch`

Important ingestion endpoint behavior:

- `GET /ingest-medicine?query=<medicine>&perPlatformLimit=all`
- `POST /ingest-batch` accepts multiple queries

The server-side ETL payload for `/scrape-details` now includes:

- `name`
- `price`
- `url`
- `platform`
- `image`
- `salt`
- `description`
- `dosage`
- `sideEffects`
- `faq`
- `sourceType`

---

## Current Query Flow

For a query like `vitamin c`, the pipeline currently works like this:

1. Query enters `ingestMedicineQuery()` in `etl/batchJob.js`
2. Discovery finds source URLs across supported platforms
3. Each URL is scraped with `scrapeProductDetail()`
4. Scraped records are transformed by `transformRecord()`
5. Canonical medicine groups are formed
6. Strict completeness is checked
7. Valid medicines are bulk-upserted
8. Platform prices are bulk-upserted
9. A summary object is returned with:
   - discovered sources
   - scraped sources
   - failed sources
   - fully detailed sources
   - strict rejected medicine count
   - ETL summary details

---

## Verified Runtime Improvements

During implementation, the repo was debugged through real runs and several runtime failures were fixed.

Resolved issues included:

- duplicate schema index warnings
- unique index build failures on `canonical_key`
- unsupported partial index expressions
- legacy text index conflicts
- Mongo bulk-write path conflicts for `canonical_key`

These fixes were necessary before the ETL could run reliably against a real MongoDB database.

---

## Image Support For App UI

One of the latest changes was adding image persistence for use in the application UI.

Current behavior:

- scraper extracts a product image when available
- transformer maps it to `image_url`
- canonical medicine document stores `image_url`
- the app can show:
  - medicine image
  - medicine name
  - salt/composition

This is especially useful for giving users confidence that the medicine shown is the correct one.

Tested examples showed valid image extraction on:

- `PharmEasy`
- `1mg`
- `Netmeds`

---

## What Is Still Weak Or Incomplete

The pipeline is significantly better than the starting point, but there are still areas that can be improved.

### 1. Apollo extraction is weaker than the others

In live checks, Apollo product detail extraction has been less reliable for:

- salt
- dosage
- full structured details

### 2. Strict completeness rejects many otherwise useful products

Because `sideEffects` is mandatory right now, many medicines are intentionally rejected even if they have:

- valid name
- valid salt
- valid price
- valid description
- valid dosage

This is correct per the current requirement, but it reduces insert volume.

### 3. Discovery is high-coverage, not perfect precision

Removing the platform cap gives much larger result sets, which is useful, but it can also pull:

- loosely related supplements
- broad vitamin products
- query-adjacent products rather than exact matches

This is partly controlled by strict insertion rules, but discovery precision can still be tuned more.

### 4. Existing README is outdated

The original `README.md` still mostly describes the early PDP-only scraper.

This new document reflects the current state more accurately.

---

## Recommended Next Steps

If work continues on this repo, the highest-value next steps are:

1. Improve Apollo product-detail extraction
2. Add stronger ranking/filtering so discovery prefers exact medicine matches before broad supplements
3. Consider a configurable strictness mode:
   - strict
   - moderate
   - permissive
4. Add a retrieval API for the app layer to fetch canonical medicines directly from MongoDB
5. Add automated smoke tests for:
   - discovery
   - transformer output
   - Mongo upsert behavior
   - image extraction
6. Update `README.md` fully so it reflects the ETL system, not just the original scraper

---

## Files Most Important Right Now

If someone wants to understand the repo quickly, these are the most important files to read:

- `server.js`
- `productScraper.js`
- `etl/batchJob.js`
- `etl/pipeline.js`
- `etl/extract/discovery.js`
- `etl/extract/platforms.js`
- `etl/transform/transformer.js`
- `etl/load/models.js`
- `etl/load/db.js`
- `etl/load/upsert.js`
- `batch-job.js`
- `test-actual.js`

---

## Current Status In One Line

This repo is no longer just a PDP link scraper; it is now a multi-platform medicine discovery, scraping, transformation, validation, and MongoDB ingestion system with batch processing, strict mandatory-field enforcement, and image support for app display.
