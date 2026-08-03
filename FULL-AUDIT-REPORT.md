# MyMedisaathi SEO Audit

Audit date: 2026-06-07
Target: https://mymedisaathi.com

## Executive Summary

Overall SEO health score: 74/100

The site fixed the earlier public-count mismatch and cleaned up utility-page canonicals, which is a real improvement. The main remaining blockers are broken medicine canonical targets and stale medicine URLs that are still present in the sitemap.

## What Looks Good

- Homepage is prerendered and returns indexable HTML.
- Title, meta description, canonical, Open Graph, and JSON-LD are present on key pages.
- `robots.txt` exists and allows the important content areas.
- `llms.txt` is publicly available and matches the live homepage count.
- Salt comparison pages carry Product and FAQ structured data.
- Utility pages no longer expose homepage canonicals.
- A random sample of live sitemap medicine URLs returned 200, so the issue is not that the entire catalog is broken.

## Critical / High Priority Findings

### 1) Medicine canonicals point to 404 URLs

Representative live medicine pages canonicalize to slugs that do not resolve:

- `https://mymedisaathi.com/medicine/simlup-20mg-tablet`
- `https://mymedisaathi.com/medicine/glycigon-tablet`
- `https://mymedisaathi.com/medicine/maxstine-m-tablet`

All three returned `404` in live checks. A separate medicine page, `https://mymedisaathi.com/medicine/ciprodac-250mg`, also returned `404`.

This is a serious indexation bug because the canonical target should be a live, crawlable URL.

### 2) Sitemap still contains stale URLs

The sitemap now contains 17,918 URLs, mostly `/medicine/` and `/salt/` pages. Targeted live checks found known stale medicine URLs such as:

- `/medicine/sr2`
- `/medicine/naprowel-plus-250mg-250mg`
- `/medicine/saface-2-2-5mg`
- `/medicine/zeogesic-sp-100mg-325mg`
- `/medicine/cardiozen-40-60`
- `/medicine/mywarf-5mg`

These remain present in `sitemap.xml` and still return `404`.

### 3) Medicine template metadata still has content-quality gaps

Representative medicine pages still show duplicated brand suffixes in the title, for example:

- `... | MediSaathi Rx India | MediSaathi Rx India`

That suggests the template is still not fully normalized for every medicine record.

## Category Notes

### Technical SEO

The site is mostly healthy technically, but the sitemap needs pruning and validation. Robots handling is sensible. The site is built for search engines, but stale medicine URLs still need removal.

### Content Quality

The core content structure is strong, and the earlier public-count mismatch is now resolved. The remaining concern is record completeness and normalization inside medicine templates.

### On-Page SEO

Key pages have usable titles and descriptions. The bigger win now is fixing canonical targets and removing title duplication, not basic metadata.

### Schema / Structured Data

Structured data is present and useful on major page types. Keep it valid and accurate.

### AI Search Readiness

`llms.txt` is a positive signal and tracks the homepage better now. Keep it generated from the same source of truth as the main site stats.

## Recommended Next Moves

1. Regenerate `sitemap.xml` from live 200 URLs only.
2. Remove stale `/medicine/` entries that return 404.
3. Make every medicine canonical resolve to a live 200 URL.
4. Clean the medicine template so titles never duplicate the brand suffix.
5. Keep public counts generated from one shared source of truth.
6. Re-sample indexable medicine pages after the cleanup.
