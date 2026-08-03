# Action Plan

## Critical

- Fix medicine canonical URLs so they resolve to live 200 pages.
- Remove stale 404 URLs from `sitemap.xml`.

## High

- Add a deploy-time sitemap validator that rejects broken URLs.
- Rebuild sitemap from live 200 pages only.
- Clean medicine template fields so titles are complete and unique.

## Medium

- Keep public counts generated from one source of truth.
- Audit metadata templates so titles and descriptions stay page-specific.
- Review noindex utility pages and confirm their canonical handling is intentional.

## Low

- Add routine monitoring for sitemap drift.
- Add a periodic sample check for medicine-page 404s.
