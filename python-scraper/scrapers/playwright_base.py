"""
playwright_base.py — Base for Playwright-backed pharmacy scrapers.

Every Playwright scraper now:
  * pulls pages from ONE shared Chromium (see browser_manager) instead of
    launching its own — the big speed + resource win,
  * reuses a cached BrowserContext per scraper so cookies/storage carry across
    medicines (helps against bot-detection),
  * blocks images/media/fonts + tracker domains to load faster & look lighter,
  * installs a unified stealth init-script on every page,
  * waits on the *data* it needs (a JSON XHR or the exact product selector)
    instead of `networkidle` / blind `asyncio.sleep`.
"""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional

from playwright.async_api import BrowserContext, Page

from scrapers import browser_manager
from scrapers.config import SCRAPER_CONFIG

logger = logging.getLogger(__name__)


# BLK-11: Decommissioned WAF evasion script (STEALTH_JS).
# Scrapers operate as standard automated browsers without spoofing navigator.webdriver,
# navigator.plugins, or browser fingerprints.
STEALTH_JS = ""


class PlaywrightBaseScraper:
    """
    Base scraper that uses Playwright to bypass WAFs and intercept API calls.
    Pages come from the process-wide shared Chromium; each scraper keeps one
    cached context for cookie/storage continuity across searches.
    """

    def __init__(self, headless: bool = True, delay: float = 1.0):
        # `headless` kept in the signature for backwards-compat; the shared
        # browser is launched from SCRAPER_CONFIG["headless"].
        self.headless = headless
        self.delay = delay
        self._context: Optional[BrowserContext] = None
        self._context_browser = None  # the Browser the cached context belongs to
        self._user_agent = random.choice(SCRAPER_CONFIG["desktop_uas"])

    # ── Lifecycle (kept for interface compatibility) ──────────────────

    async def start(self):
        """Ensure the shared browser is up. Contexts are created lazily."""
        await browser_manager.get_browser()

    async def stop(self):
        """Release THIS scraper's cached context (never the shared browser)."""
        await self._close_context()

    def close(self):
        """Synchronous close for interface compat.

        The shared browser is torn down once, in-loop, by the caller
        (see browser_manager.shutdown). Here we only drop references — doing
        async work from a dead loop is what caused the old 'closed pipe' errors.
        """
        self._context = None
        self._context_browser = None

    # ── Context / page management ─────────────────────────────────────

    async def _close_context(self):
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug(f"[{self._name}] context close ignored: {e}")
        self._context = None
        self._context_browser = None

    async def _get_context(self) -> BrowserContext:
        """Return this scraper's context, (re)creating it if stale."""
        browser = await browser_manager.get_browser()
        # If the shared browser was relaunched, our old context is dead.
        if self._context is not None and self._context_browser is not browser:
            await self._close_context()

        if self._context is None:
            self._context = await browser.new_context(
                user_agent=self._user_agent,
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Referer": "https://www.google.com/",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            self._context_browser = browser
            if SCRAPER_CONFIG["block_resources"]:
                await self._context.route("**/*", self._route_filter)

        return self._context

    async def _route_filter(self, route):
        """Abort heavy assets & trackers; NEVER script/xhr/fetch/stylesheet."""
        try:
            req = route.request
            if req.resource_type in SCRAPER_CONFIG["blocked_resource_types"]:
                await route.abort()
                return
            url = req.url
            for snippet in SCRAPER_CONFIG["blocked_url_snippets"]:
                if snippet in url:
                    await route.abort()
                    return
            await route.continue_()
        except Exception:
            # A failed route must never stall navigation.
            try:
                await route.continue_()
            except Exception:
                pass

    async def get_page(self) -> Page:
        """Create a fresh page in the cached context (rebuilds context if dead)."""
        try:
            context = await self._get_context()
            return await context.new_page()
        except Exception as e:
            logger.warning(f"[{self._name}] context dead ({e}); rebuilding.")
            await self._close_context()
            context = await self._get_context()
            return await context.new_page()

    # ── Data-driven waiting ───────────────────────────────────────────

    async def goto_and_wait(
        self,
        page: Page,
        url: str,
        wait_selector: Optional[str] = None,
        response_substr: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Navigate to `url` on `domcontentloaded`, then wait for real data:
          * if `response_substr` given, return the parsed JSON of the first
            matching 2xx XHR (or None if it never arrives),
          * else if `wait_selector` given, wait for that selector to appear.

        Navigation timeouts propagate; selector timeouts are swallowed so the
        caller can still parse whatever rendered.
        """
        nav_timeout = SCRAPER_CONFIG["nav_timeout_ms"]
        sel_timeout = SCRAPER_CONFIG["selector_timeout_ms"]

        if response_substr:
            try:
                async with page.expect_response(
                    lambda r: response_substr in r.url and r.status == 200,
                    timeout=sel_timeout,
                ) as resp_info:
                    await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
                resp = await resp_info.value
                return await resp.json()
            except Exception as e:
                logger.debug(f"[{self._name}] XHR '{response_substr}' not captured: {e}")
                return None

        await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
        if wait_selector:
            await self.wait_selector(page, wait_selector)
        return None

    async def wait_selector(self, page: Page, selector: str) -> bool:
        """Wait for a selector; return False (not raise) on timeout."""
        try:
            await page.wait_for_selector(
                selector, timeout=SCRAPER_CONFIG["selector_timeout_ms"]
            )
            return True
        except Exception:
            logger.debug(f"[{self._name}] selector '{selector}' not found in time.")
            return False

    # ── Retry wrapper ─────────────────────────────────────────────────

    async def retry_search(
        self, attempt_fn: Callable[[], Awaitable[List[Dict]]]
    ) -> List[Dict]:
        """
        Run one search attempt; on empty/exception retry with a fresh context
        and rotated user agent, up to SCRAPER_CONFIG['search_retries'] total.
        """
        retries = max(1, SCRAPER_CONFIG["search_retries"])
        backoff = SCRAPER_CONFIG["retry_backoff_s"]
        results: List[Dict] = []

        for attempt in range(1, retries + 1):
            try:
                results = await attempt_fn()
            except Exception as e:
                logger.warning(f"[{self._name}] attempt {attempt}/{retries} error: {e}")
                results = []

            if results:
                return results

            if attempt < retries:
                logger.info(f"[{self._name}] empty result, retrying ({attempt}/{retries})...")
                # fresh identity + fresh context for the retry
                self._user_agent = random.choice(SCRAPER_CONFIG["desktop_uas"])
                await self._close_context()
                await asyncio.sleep(backoff * attempt)

        return results

    @property
    def _name(self) -> str:
        return getattr(self, "platform_name", self.__class__.__name__)

    # ── Optional: generic JSON-API interception ───────────────────────

    async def intercept_api(self, url: str, api_match_str: str) -> Optional[Dict[str, Any]]:
        """Navigate to `url` and return the first matching 2xx JSON XHR body."""
        page = await self.get_page()
        try:
            return await self.goto_and_wait(page, url, response_substr=api_match_str)
        except Exception as e:
            logger.error(f"[{self._name}] intercept_api error for {url}: {e}")
            return None
        finally:
            try:
                await page.close()
            except Exception:
                pass

