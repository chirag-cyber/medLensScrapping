import asyncio
import logging
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, Page

logger = logging.getLogger(__name__)

class PlaywrightBaseScraper:
    """
    Base scraper that uses Playwright to bypass WAFs and intercept API calls.
    Must be used within an async context.
    """
    
    def __init__(self, headless: bool = True, delay: float = 1.0):
        self.headless = headless
        self.delay = delay
        self.playwright = None
        self.browser: Optional[Browser] = None
        
    async def start(self):
        """Start the playwright browser session."""
        if not self.playwright:
            self.playwright = await async_playwright().start()
        if not self.browser:
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
    async def stop(self):
        """Close the browser session and playwright instance."""
        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            logger.info(f"Playwright stopped successfully.")
        except Exception as e:
            logger.debug(f"Error during Playwright stop: {e}")

    def close(self):
        """Synchronous close for compatibility with the interface."""
        if self.browser or self.playwright:
            try:
                # We try to run stop in the current loop if it exists
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.stop())
                else:
                    loop.run_until_complete(self.stop())
            except Exception as e:
                logger.debug(f"Could not perform synchronous close: {e}")

    async def get_page(self) -> Page:
        """Create a new page with stealth headers and viewport."""
        try:
            if self.browser and not self.browser.is_connected():
                await self.stop()
            if not self.browser:
                await self.start()
                
            context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
                viewport={'width': 375, 'height': 812},
                extra_http_headers={
                    "Referer": "https://www.google.com/",
                    "Accept-Language": "en-US,en;q=0.9"
                }
            )
        except Exception as e:
            logger.warning(f"Browser connection lost ({e}). Restarting Playwright...")
            await self.stop()
            await self.start()
            context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
                viewport={'width': 375, 'height': 812},
                extra_http_headers={
                    "Referer": "https://www.google.com/",
                    "Accept-Language": "en-US,en;q=0.9"
                }
            )
        
        page = await context.new_page()
        
        # Simple stealth script injection
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        return page

    async def intercept_api(self, url: str, api_match_str: str) -> Optional[Dict[str, Any]]:
        """
        Navigate to a URL and intercept a specific JSON API call response.
        Returns the parsed JSON response.
        """
        page = await self.get_page()
        result_data = None
        
        # We need to capture the response from the API endpoint
        async def handle_response(response):
            nonlocal result_data
            if api_match_str in response.url and response.status == 200:
                try:
                    result_data = await response.json()
                    logger.info(f"Successfully intercepted API: {response.url[:100]}")
                except Exception as e:
                    logger.debug(f"Failed to parse JSON from {response.url}: {e}")

        page.on("response", handle_response)
        
        try:
            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="networkidle", timeout=15000)
            
            # Wait a bit extra to ensure API calls finish
            await asyncio.sleep(self.delay)
            
        except Exception as e:
            logger.error(f"Error navigating to {url}: {e}")
            
        finally:
            await page.context.close()
            
        return result_data
