"""
config.py — Central tuning knobs for the scraping system.

Every timeout, retry count, resource-block list, and user-agent lives here so
behavior can be tuned in one place instead of hunting through each scraper.
"""

SCRAPER_CONFIG = {
    # ── Browser ──
    "headless": True,
    "nav_timeout_ms": 20000,        # page.goto ceiling
    "selector_timeout_ms": 8000,    # wait_for_selector / expect_response ceiling

    # ── Search resilience ──
    "search_retries": 2,            # total attempts per platform search (1 initial + 1 retry)
    "retry_backoff_s": 1.5,         # base backoff between retries (scaled by attempt)

    # ── Politeness ──
    "polite_delay_s": 0.3,          # default per-scraper delay (kept for compat)

    # ── Resource blocking (speed + smaller fingerprint) ──
    "block_resources": True,
    # NEVER block script / xhr / fetch / stylesheet — the SPAs need them to render
    # and the JSON XHRs are our data source.
    "blocked_resource_types": {"image", "media", "font"},
    "blocked_url_snippets": [
        "google-analytics", "googletagmanager", "doubleclick", "facebook",
        "hotjar", "clarity.ms", "segment", "mixpanel", "criteo", "moengage",
        "freshchat", "clevertap", "gtag/js", "/pixel", "adservice",
    ],

    # ── Debug ──
    "debug_dump_html": False,       # gate the medplus_debug.html write

    # ── User agents (rotated per context; desktop-class so parser DOM stays stable) ──
    "desktop_uas": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    ],
}
