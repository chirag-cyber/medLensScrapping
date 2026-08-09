# Scraper System Guide

A complete catalog of every active Python file in the scraper system: what it does, how to run it, and whether it writes to the database. Use this as the reference for what tooling exists to **scrape**, **heal/modify**, **delete/clean**, **enrich**, and **inspect** your data.

## How to read this guide

Each entry lists:

- **Purpose** — what the file does in one line.
- **Category** — where it fits (see legend below).
- **Run** — the command to invoke it (or "imported module" if it is a library, not a script).
- **DB write** — whether it modifies MongoDB, and what safety gate (if any) protects that write.

### Category legend

| Category | Meaning |
| --- | --- |
| SCRAPE | Fetches data from a pharmacy platform. Returns data; does not write to the DB itself. |
| SYNC | Orchestrates scraping and writes results (prices, medicines) to the DB. |
| ENRICH | Fills clinical fields (uses, side effects, safety advice, manufacturer) on existing medicines. |
| HEAL/MODIFY | Repairs or normalizes existing records in place. |
| DELETE/CLEANUP | Removes bad or duplicate rows. |
| INSPECT/REPORT | Read-only audits and counts. |
| LIB/SHARED | Imported modules; no direct run, no DB write. |
| ORCHESTRATION | Cron-ready wrappers that chain the above. |
| TEST | Unit tests. |

### Safety-gate conventions

- **`--dry-run` default** — the script reports what it *would* change and writes nothing unless you pass `--apply`. Safest pattern.
- **`--dry-run` flag** — writes by default; pass `--dry-run` to preview.
- **No safety gate** — writes directly on every run. Take a backup first (see `tools/backup_collections.py`).

> **Before any destructive or `--apply` run against production: back up first** with `python tools/backup_collections.py`, then run the dry-run, then apply.

---

## Root scripts

### sync_and_scrape.py
- **Purpose:** Main sync engine — scrapes prices and clinical data from all 8 platforms for medicines in the DB.
- **Category:** SYNC
- **Run:** `python sync_and_scrape.py --sync-all [--update-clinical] [--skip N] [--reset]`
- **Also:** `--scrape "NAME" [--url URL ...]` adds one new medicine; `--seed-url "NAME" "URL"` attaches a product-page URL to a medicine that already exists. See [Adding a medicine search cannot find](#adding-a-medicine-search-cannot-find-pdp-fallback).
- **DB write:** YES (medicines, prices, price_history). No safety gate — writes directly.

### targeted_resync.py
- **Purpose:** Re-scrape a specific set of medicines from a comma list or a file.
- **Category:** SYNC
- **Run:** `python targeted_resync.py --medicines "name1,name2"` or `--file path.txt`
- **DB write:** YES (prices). No safety gate.

### dedup_medicines.py
- **Purpose:** Find duplicate medicine records, merge them, backfill normalized_salt.
- **Category:** HEAL/MODIFY
- **Run:** `python dedup_medicines.py [--dry-run | --apply]`
- **DB write:** YES (medicines, prices, history). Safety: `--dry-run` (default) / `--apply`.

### heal_salts.py
- **Purpose:** Fix standalone records with wrong normalized_salt values.
- **Category:** HEAL/MODIFY
- **Run:** `python heal_salts.py [--dry-run | --apply] [--salt NAME]`
- **DB write:** YES (medicines.normalized_salt). Safety: `--dry-run` (default) / `--apply`.

### canonicalize_salt_order.py
- **Purpose:** Merge salt buckets split by word-order variants (e.g. "formoterol budesonide" vs "budesonide formoterol").
- **Category:** HEAL/MODIFY
- **Run:** `python canonicalize_salt_order.py [--dry-run | --apply] [--salt NAME]`
- **DB write:** YES (medicines.normalized_salt). Safety: `--dry-run` (default) / `--apply`.

### heal_manufacturer.py
- **Purpose:** Blank false platform-name manufacturers (Apollo Partner, Apollo Pharmacy, Amazon Seller, Tata 1mg, etc.) that were captured as the maker when the real manufacturer differs. Writes a rescrape worklist (`manufacturer_rescrape.txt`) so blanked records can be refilled.
- **Category:** HEAL/MODIFY
- **Run:** `python heal_manufacturer.py [--dry-run | --apply] [--backup PATH]`
- **DB write:** YES (medicines.manufacturer). Safety: `--dry-run` (default) / `--apply`; `--backup` dumps the manufacturer field before writing.
- **Recovery path:** After blanking, `enrichment/enrich_clinical_v2.py` re-selects records where `manufacturer == ''` and refills the real maker from the 1mg/Netmeds detail page (`marketer.legalName`).

### medicine_identity.py
- **Purpose:** Pure identity/dedup resolver — `parse_identity`, `is_same_medicine` helpers.
- **Category:** LIB/SHARED
- **Run:** Imported module, not run directly.
- **DB write:** NO.

### detail_scraper.py
- **Purpose:** Scrape comprehensive clinical data (including the real manufacturer) from 1mg product pages.
- **Category:** SCRAPE
- **Run:** `python detail_scraper.py [URL]` (test mode — fetches one URL)
- **DB write:** NO (returns data; callers write).

### search_engine.py
- **Purpose:** Multi-platform search engine with relevance scoring.
- **Category:** SCRAPE
- **Run:** `python search_engine.py [QUERY]` (test mode — searches all platforms)
- **DB write:** NO.

### scheduled_runner.py
- **Purpose:** Cron-ready wrapper — runs sync plus optional dedup, with file logging and a lock file.
- **Category:** ORCHESTRATION
- **Run:** `python scheduled_runner.py [--update-clinical] [--no-dedup] [--dedup-apply] [--llm-backfill]`
- **DB write:** YES (delegates to sync_and_scrape + dedup_medicines). Safety: `--dedup-apply` gates dedup writes; sync writes directly.

---

## Scrapers (`scrapers/`)

All scrapers return data to their caller and do **not** write to the DB.

| File | Purpose | Run |
| --- | --- | --- |
| `__init__.py` | Package exports for OneMGScraper, NetmedsScraper, BaseScraper | imported |
| `base.py` | Resilient HTTP client base with retry, rate-limit, rotating UAs | imported |
| `interface.py` | PharmacyScraper interface + shared out-of-stock detection helpers | imported |
| `playwright_base.py` | Playwright scraper base with shared Chromium, stealth, resource blocking | imported |
| `browser_manager.py` | One shared Chromium process for all Playwright scrapers | imported |
| `config.py` | Central tuning knobs (timeouts, retries, resource-block lists) | imported |
| `apollo.py` | Apollo Pharmacy scraper (Playwright DOM parser, bypasses WAF) | `python -m scrapers.apollo` |
| `amazon_pharmacy.py` | Amazon Pharmacy scraper (HTTP + BeautifulSoup) | `python -m scrapers.amazon_pharmacy` |
| `platinumrx.py` | PlatinumRx scraper (internal API, no Playwright) | imported |
| `truemeds.py` | Truemeds scraper (secret API nal.tmmumbai.in) | imported |
| `medplus.py` | Medplus scraper (Playwright with base64 tile decoding) | `python -m scrapers.medplus` |
| `netmeds.py` | Netmeds clinical scraper (HTTP + BeautifulSoup) | imported |
| `netmeds_search.py` | Netmeds search scraper (Playwright with XHR intercept) | `python -m scrapers.netmeds_search` |
| `onemg.py` | 1mg clinical scraper (HTTP + BeautifulSoup) | imported |
| `onemg_search.py` | 1mg search scraper (Playwright Locators) | `python -m scrapers.onemg_search` |
| `pharmeasy.py` | PharmEasy scraper (HTTP + BeautifulSoup) | `python -m scrapers.pharmeasy` |
| `pdp_fetch.py` | Reads one known product-page URL directly, skipping search. Platform-agnostic: JSON-LD, then Next.js payload, then embedded state | imported |

> **Manufacturer note:** The search-result scrapers (apollo, amazon_pharmacy, platinumrx, truemeds, medplus) now emit an empty manufacturer (`""`) rather than a platform self-name. The real manufacturer is populated only from the detail page (`detail_scraper.py` / 1mg / Netmeds `marketer.legalName`). The platform is recorded separately in the `platform` field.

---

## Enrichment (`enrichment/`)

### enrich.py
- **Purpose:** Multi-phase enrichment pipeline (1mg → siblings → Netmeds → siblings).
- **Category:** ENRICH
- **Run:** `python enrichment/enrich.py [--phase 1|2|3|4|all] [--limit N] [--offset N] [--delay S] [--force] [--status]`
- **DB write:** YES (medicines clinical fields). No safety gate.

### enrich_clinical.py
- **Purpose:** Enrich from 1mg/Netmeds via Jina Reader.
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_clinical.py [--limit N] [--batch N] [--delay S] [--force] [--offset N]`
- **DB write:** YES (medicines). No safety gate.

### enrich_clinical_v2.py
- **Purpose:** Safe 1mg enrichment with non-destructive updates plus malformed-data cleanup. Also the recovery path for blanked manufacturers — it re-selects records where `manufacturer` is empty and refills the real maker.
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_clinical_v2.py [--dry-run] [--limit N] [--batch N] [--delay S] [--force] [--offset N] [--fix-only]`
- **DB write:** YES (medicines). Safety: `--dry-run`.

### enrich_from_1mg.py
- **Purpose:** Enrich from 1mg via Jina Reader.
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_from_1mg.py [--limit N] [--batch N] [--delay S] [--force] [--offset N]`
- **DB write:** YES (medicines). No safety gate.

### enrich_from_siblings.py
- **Purpose:** Copy clinical data from same-salt sibling medicines (no API calls).
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_from_siblings.py [--limit N] [--dry-run]`
- **DB write:** YES (medicines). Safety: `--dry-run`.

### enrich_from_medcompare.py
- **Purpose:** Enrich from the MedCompare `/api/medicine-info` endpoint.
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_from_medcompare.py [--limit N] [--delay S] [--offset N]`
- **DB write:** YES (medicines). No safety gate.

### enrich_remaining.py
- **Purpose:** Final pass — enrich remaining medicines via Netmeds/1mg scraping.
- **Category:** ENRICH
- **Run:** `python enrichment/enrich_remaining.py [--limit N] [--delay S] [--offset N]`
- **DB write:** YES (medicines). No safety gate.

### update_db_clinical.py
- **Purpose:** Update missing clinical data from 1mg (async fetch + update).
- **Category:** ENRICH
- **Run:** `python enrichment/update_db_clinical.py`
- **DB write:** YES (medicines). No safety gate.

### clean_side_effects.py
- **Purpose:** Clean malformed side_effects arrays (remove noise, split strings).
- **Category:** HEAL/MODIFY
- **Run:** `python enrichment/clean_side_effects.py`
- **DB write:** YES (medicines.side_effects). No safety gate.

### fill_missing_side_effects.py
- **Purpose:** Fill missing side_effects by scraping 1mg for those medicines.
- **Category:** ENRICH
- **Run:** `python enrichment/fill_missing_side_effects.py`
- **DB write:** YES (medicines.side_effects). No safety gate.

### set_default_side_effects.py
- **Purpose:** Set placeholder `['No common side effects reported']` for empty side_effects.
- **Category:** ENRICH
- **Run:** `python enrichment/set_default_side_effects.py`
- **DB write:** YES (medicines.side_effects). No safety gate.

### llm_side_effects_fix.py
- **Purpose:** LLM backfill of placeholder side_effects via Groq (deduped by salt).
- **Category:** ENRICH
- **Run:** `python enrichment/llm_side_effects_fix.py`
- **DB write:** YES (medicines.side_effects). No safety gate.

---

## Tools (`tools/`)

### backup_collections.py
- **Purpose:** Create timestamped backups of the medicines and prices collections. Run this before any destructive operation.
- **Category:** INSPECT/REPORT
- **Run:** `python tools/backup_collections.py`
- **DB write:** NO (creates backup collections; reads only from source).

### inspect_db.py
- **Purpose:** Find and categorize malformed clinical data (audit report).
- **Category:** INSPECT/REPORT
- **Run:** `python tools/inspect_db.py`
- **DB write:** NO.

### check_db.py
- **Purpose:** Count PlatinumRx prices in the DB (simple query).
- **Category:** INSPECT/REPORT
- **Run:** `python tools/check_db.py`
- **DB write:** NO.

### check_coverage.py
- **Purpose:** Check unique salt count and enrichment coverage stats.
- **Category:** INSPECT/REPORT
- **Run:** `python tools/check_coverage.py`
- **DB write:** NO.

### debug.py
- **Purpose:** Debug medicine/price matching for Ibugesic (list matches).
- **Category:** INSPECT/REPORT
- **Run:** `python tools/debug.py`
- **DB write:** NO.

### migrate_platform_casing.py
- **Purpose:** Lowercase the platform field and delete duplicate (medicine_id, platform) rows.
- **Category:** HEAL/MODIFY
- **Run:** `python tools/migrate_platform_casing.py`
- **DB write:** YES (prices). No safety gate.

### cleanup_bad_matches.py
- **Purpose:** Delete price rows where the brand name is missing from the price name/URL.
- **Category:** DELETE/CLEANUP
- **Run:** `python tools/cleanup_bad_matches.py`
- **DB write:** YES (prices). No safety gate — deletes immediately.

### clean_ibugesic.py
- **Purpose:** Delete bad Ibugesic price matches (hardcoded cleanup).
- **Category:** DELETE/CLEANUP
- **Run:** `python tools/clean_ibugesic.py`
- **DB write:** YES (prices). No safety gate.

### find_suspects.py
- **Purpose:** Find suspect price matches (brand not in price name/URL) — report only.
- **Category:** INSPECT/REPORT
- **Run:** `python tools/find_suspects.py`
- **DB write:** NO.

---

## Tests (`tests/`)

### test_medicine_identity.py
- **Purpose:** Unit tests for medicine_identity.py (parse_identity, is_same_medicine).
- **Run:** `python -m unittest tests.test_medicine_identity -v`
- **DB write:** NO.

### test_relevance.py
- **Purpose:** Unit tests for search_engine.compute_relevance_score.
- **Run:** `python -m unittest tests.test_relevance -v`
- **DB write:** NO.

---

## Adding a medicine search cannot find (PDP fallback)

Some brands exist on a pharmacy's site but never surface in that pharmacy's own
search. TEARDAY PLUS is the worked example: all 8 platforms return same-salt
substitutes instead (Tears Plus, Tearcag-Plus, Sensieyes Plus, Telday), every one
of them scores 0.00 against the query, and the run ends with:

```
[pdp] 8 platform(s) missing for 'TEARDAY PLUS' and no stored URL to re-fetch
No relevant results matched 'TEARDAY PLUS'.
```

The PDP (product detail page) fallback exists for exactly this: given a URL, the
page is fetched directly and search is skipped.

### How the fallback picks its URLs

`_pdp_backfill` in `sync_and_scrape.py` draws from two sources:

- **Stored URLs** — the `url` recorded on a previous price row for that medicine.
  Used only to fill a platform that search *missed* on this run.
- **Seeded URLs** — hand-collected URLs passed on the command line. Always
  fetched, even for a platform search already covered, and they take precedence
  over a stored URL for the same platform.

Seeded URLs always compete because a human-verified URL is better evidence than a
search hit that merely cleared the 0.2 relevance floor. Both candidates go into
`pick_best_per_platform` and the higher-scoring row wins, so seeding can only
improve the pick.

The platform is inferred from the URL host (`_HOST_PLATFORM`), never from
anything the caller types, so a URL cannot be filed under the wrong pharmacy. An
unrecognized host is logged and dropped rather than guessed at.

### Which flag to use

Both exist because they solve different halves of the problem.

| Situation | Command |
| --- | --- |
| Medicine is **not** in the DB yet | `python sync_and_scrape.py --scrape "NAME" --url "URL" [--url "URL" ...]` |
| Medicine **is** already in the DB | `python sync_and_scrape.py --seed-url "NAME" "URL"` |

`--seed-url` alone cannot rescue a brand that has never been scraped. It looks
the medicine up in the `medicines` collection and refuses if it is absent — and a
medicine only gets inserted *after* a run finds at least one relevant result. For
a brand no search will surface, that never happens. `--scrape … --url …` is the
way in; after the first successful run the URL is stored on the price row and
every later sync re-fetches it automatically, with no flag needed.

`--url` is repeatable — pass one per pharmacy:

```bash
python sync_and_scrape.py --scrape "TEARDAY PLUS" \
  --url "https://www.netmeds.com/non-prescriptions/..." \
  --url "https://www.1mg.com/otc/..."
```

### What a seeded URL does *not* buy you

It says where to look. It does not say what the price is.

A page fetched from a seeded URL passes the same `compute_relevance_score` and
salt validation as any search result. A wrong or stale URL is rejected, not
published as that medicine's price. That is deliberate: a hand-collected link is
easy to paste against the wrong strength or a delisted pack, and the cost of
being wrong here is a customer seeing a price that is not real.

So a seeded run can still legitimately end in "No relevant results matched" — that
means the page did not match the medicine, and the correct fix is a better URL,
not a lower floor.

### Where the URLs live

Nowhere, by design — they are typed on the command line and stored on the price
row after the first successful scrape. There is no seed file in this repo. If you
have collected URLs by hand and want them to survive, keep them outside the repo
or feed them through `--url` once so they land in the DB.

---

## Quick reference

**Scraping & sync**
```bash
python sync_and_scrape.py --sync-all [--update-clinical]
python targeted_resync.py --file resync_list.txt
python scheduled_runner.py [--dedup-apply] [--llm-backfill]
```

**Adding one medicine (PDP fallback when search cannot find it)**
```bash
python sync_and_scrape.py --scrape "NAME" --url "https://www.netmeds.com/..." --url "https://www.1mg.com/..."
python sync_and_scrape.py --seed-url "NAME" "https://www.netmeds.com/..."
```

**Healing & dedup (all support `--dry-run` / `--apply`)**
```bash
python dedup_medicines.py --apply
python heal_salts.py --apply
python canonicalize_salt_order.py --apply
python heal_manufacturer.py --apply
```

**Enrichment (writes directly; `--dry-run` where noted)**
```bash
python enrichment/enrich_clinical_v2.py --dry-run --limit 10
python enrichment/enrich_from_siblings.py --dry-run
python enrichment/enrich.py --phase all
python enrichment/llm_side_effects_fix.py
```

**Cleanup (DELETE — no safety gate, back up first)**
```bash
python tools/cleanup_bad_matches.py
python tools/clean_ibugesic.py
python tools/migrate_platform_casing.py
```

**Inspection (read-only)**
```bash
python tools/inspect_db.py
python tools/check_coverage.py
python tools/find_suspects.py
python tools/backup_collections.py
```

**Tests**
```bash
python -m unittest tests.test_medicine_identity -v
python -m unittest tests.test_relevance -v
```
