"""
browser_manager.py — One shared Chromium for all Playwright scrapers.

Instead of each scraper launching (and tearing down) its own browser per
medicine, every Playwright-backed scraper pulls pages from a single
process-wide Chromium started here. This is the biggest speed + resource win:
a `--sync-all` over thousands of medicines relaunches a handful of browsers
instead of thousands.

The browser is launched lazily, guarded by a lock so concurrent first-callers
don't race, and auto-relaunched if it becomes detached/closed.
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright

from scrapers.config import SCRAPER_CONFIG

logger = logging.getLogger(__name__)

_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """Lazily create the asyncio.Lock inside the running event loop.
    Creating it at module-import time (outside any loop) causes
    'Future attached to a different loop' when asyncio.run() spins up
    a new loop in a background thread (APScheduler / gunicorn)."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


async def get_browser() -> Browser:
    """Return the process-wide shared browser, (re)launching if absent/detached."""
    global _playwright, _browser

    if _is_alive(_browser):
        return _browser

    async with _get_lock():
        # Re-check under the lock: another task may have launched while we waited.
        if _is_alive(_browser):
            return _browser

        await _discard_browser_locked()

        if _playwright is None:
            _playwright = await async_playwright().start()

        try:
            _browser = await _playwright.chromium.launch(
                headless=SCRAPER_CONFIG["headless"], args=_LAUNCH_ARGS
            )
        except Exception as e:
            # The Playwright instance itself may be stale — restart it fully.
            logger.warning(f"Chromium launch failed ({e}); restarting Playwright.")
            try:
                await _playwright.stop()
            except Exception:
                pass
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=SCRAPER_CONFIG["headless"], args=_LAUNCH_ARGS
            )

        logger.info("Shared Chromium launched.")
        return _browser


async def shutdown():
    """Close the shared browser and stop Playwright. Call once, inside a live loop."""
    global _playwright, _browser
    async with _get_lock():
        await _discard_browser_locked()
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception as e:
                logger.debug(f"playwright stop ignored: {e}")
        _playwright = None


def _is_alive(browser: Optional[Browser]) -> bool:
    if browser is None:
        return False
    try:
        return browser.is_connected()
    except Exception:
        return False


async def _discard_browser_locked():
    """Close the current browser if any. Assumes the caller holds `_lock`."""
    global _browser
    if _browser is not None:
        try:
            if _browser.is_connected():
                await _browser.close()
        except Exception as e:
            logger.debug(f"browser close ignored: {e}")
    _browser = None
