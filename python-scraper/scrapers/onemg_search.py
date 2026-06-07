import asyncio
import logging
import re
from typing import List, Dict
from scrapers.playwright_base import PlaywrightBaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class OneMgSearchScraper(PlaywrightBaseScraper, PharmacyScraper):
    """
    1mg search scraper using Playwright Locators (from verified GitHub script).
    Uses class*= partial matching which survives CSS hash changes.
    """

    BASE_URL = "https://www.1mg.com"

    @property
    def platform_name(self) -> str:
        return "1mg"

    async def get_page(self):
        """Override to use desktop user agent for 1mg, as the locators are for desktop UI."""
        if not self.browser:
            await self.start()
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        return await context.new_page()

    def search_medicine(self, query: str) -> List[Dict]:
        return asyncio.run(self.search_async(query))

    async def search_async(self, query: str) -> List[Dict]:
        url = f"{self.BASE_URL}/search/all?name={query.replace(' ', '+')}"

        page = await self.get_page()
        try:
            logger.info(f"[{self.platform_name}] Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2500)

            # Use the proven GitHub selectors
            cards = await page.locator('[class*="VerticalProductTile__container"]').all()
            logger.info(f"[{self.platform_name}] Found {len(cards)} product tiles.")

            standardized_results = []
            for card in cards:
                try:
                    # --- Name ---
                    name_el = card.locator('[class*="VerticalProductTile__header"]').first
                    name = ""
                    if await name_el.count() > 0:
                        name = (await name_el.inner_text()).strip()
                    if not name:
                        continue

                    # --- URL ---
                    header_el = card.locator('[class*="VerticalProductTile__header"]').first
                    link = ""
                    if await header_el.count() > 0:
                        parent_a = header_el.locator("xpath=ancestor::a").first
                        if await parent_a.count() > 0:
                            href = await parent_a.get_attribute("href")
                            link = f"{self.BASE_URL}{href}" if href else ""
                    if not link:
                        continue

                    # --- Selling Price (Discounted Price) ---
                    selling_price = 0.0
                    sell_el = card.locator("text=Discounted Price").first
                    if await sell_el.count() > 0:
                        parent = sell_el.locator("xpath=..").first
                        txt = (await parent.inner_text()).strip()
                        price_match = re.search(r'[\u20b9₹]?\s*([\d,.]+)', txt)
                        if price_match:
                            selling_price = float(price_match.group(1).replace(',', ''))

                    # --- MRP (Original Price) ---
                    mrp = 0.0
                    orig_el = card.locator("text=Original Price").first
                    if await orig_el.count() > 0:
                        parent = orig_el.locator("xpath=..").first
                        txt = (await parent.inner_text()).strip()
                        price_match = re.search(r'[\u20b9₹]?\s*([\d,.]+)', txt)
                        if price_match:
                            mrp = float(price_match.group(1).replace(',', ''))

                    if selling_price <= 0:
                        continue
                    if mrp <= 0 or mrp < selling_price:
                        mrp = selling_price

                    # --- Pack Size ---
                    card_text = await card.inner_text()
                    pack_size = ""
                    pack_match = re.search(
                        r'strip of (\d+ \w+)|bottle of (\d+ \w+)|(\d+ \w+) in (?:strip|tablet|capsule)',
                        card_text, re.IGNORECASE
                    )
                    if pack_match:
                        pack_size = pack_match.group()
                    else:
                        pack_match2 = re.search(r'of (\d+\s*\w+)', card_text, re.IGNORECASE)
                        if pack_match2:
                            pack_size = pack_match2.group()

                    standardized_results.append(self._standardize_result(
                        name=name,
                        url=link,
                        mrp=mrp,
                        sale_price=selling_price,
                        pack_size=pack_size,
                        manufacturer="",
                        in_stock=True
                    ))

                except Exception as e:
                    logger.debug(f"[{self.platform_name}] Card parse error: {e}")
                    continue

            return standardized_results

        except Exception as e:
            logger.error(f"[{self.platform_name}] Error: {e}")
            return []
        finally:
            await page.context.close()


if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(level=logging.INFO)

    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    scraper = OneMgSearchScraper()

    print(f"Testing 1mg Scraper for: '{query}'")
    results = scraper.search_medicine(query)
    print(f"Found {len(results)} results.")
    for r in results[:5]:
        print(json.dumps(r, indent=2))
