# MyMedisaathi SEO Audit

Audit date: 2026-06-06
Target: https://mymedisaathi.com

## Executive Summary

Overall SEO health score: 76/100

The deploy fixed the earlier trust mismatch: the homepage and `llms.txt` now agree on the core pharmacy count, and utility pages no longer expose homepage canonicals. The biggest remaining blockers are stale medicine URLs in the sitemap and broken medicine canonical targets that point to 404 pages.

## What Looks Good

- Homepage is prerendered and returns indexable HTML.
- Title, meta description, canonical, Open Graph, and JSON-LD are present on key pages.
- `robots.txt` exists and allows the important content areas.
- `llms.txt` is publicly available and now matches the live homepage count.
- Salt comparison pages carry Product and FAQ structured data.
- Utility pages now look cleaner; the earlier homepage canonical issue is no longer visible on `/upload`, `/profile`, or `/savings`.

## Critical / High Priority Findings

### 1) Medicine canonicals point to 404 URLs

Representative medicine pages canonicalize to slugs that do not resolve:

- `https://mymedisaathi.com/medicine/a-clav-375-tablet`
- `https://mymedisaathi.com/medicine/ciprodac-250mg-tablets`
- `https://mymedisaathi.com/medicine/empalo-25-tablet`

All three returned `404` in live checks. This is a serious indexation bug.

### 2) Sitemap still contains stale URLs

The sitemap now contains 17,917 URLs, mostly `/medicine/` and `/salt/` pages. A sample of 20 medicine URLs produced 6 broken 404s, including:

- `https://mymedisaathi.com/medicine/sr2`
- `https://mymedisaathi.com/medicine/naprowel-plus-250mg-250mg`
- `https://mymedisaathi.com/medicine/amlodac-2-5mg`
- `https://mymedisaathi.com/medicine/saface-2-2-5mg`
- `https://mymedisaathi.com/medicine/urilosin-0-4mg`
- `https://mymedisaathi.com/medicine/zeogesic-sp-100mg-325mg`

This is still a crawl-budget and index-quality problem.

### 3) Medicine template metadata still has content-quality gaps

A representative medicine page still shows a duplicated brand suffix in the title and an incomplete description fragment like `Contains .`.

That suggests the template is still not fully populated for every medicine record.

## Category Notes

### Technical SEO

The site is mostly healthy technically, but the sitemap needs pruning and validation. Robots handling is sensible. The site is built for search engines, but stale URLs need removal.

### Content Quality

The core content structure is strong, and the earlier public-count mismatch is now resolved. The remaining concern is record completeness inside medicine templates.

### On-Page SEO

Key pages have usable titles and descriptions. The bigger win is consistency and template hygiene, not basic metadata.

### Schema / Structured Data

Structured data is present and useful on major page types. Keep it valid and accurate.

### AI Search Readiness

`llms.txt` is a positive signal and now tracks the homepage better. Keep it generated from the same source of truth as the main site stats.

## Recommended Next Moves

1. Regenerate `sitemap.xml` from live 200 URLs only.
2. Remove stale `/medicine/` entries that return 404.
3. Make every medicine canonical resolve to a live 200 URL.
4. Clean the medicine template so titles and descriptions never contain missing fragments like `Contains .`.
5. Keep public counts generated from one shared source of truth.
6. Re-sample indexable medicine pages after the cleanup.
