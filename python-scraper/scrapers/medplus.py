import asyncio
import logging
import re
import base64
from typing import List, Dict
from scrapers.playwright_base import PlaywrightBaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class MedplusScraper(PlaywrightBaseScraper, PharmacyScraper):
    """
    Medplus scraper using Playwright and the Base64 search logic.
    """

    BASE_URL = "https://www.medplusmart.com"

    # Medplus renders product cards with class 'product-card'.
    PRODUCT_SELECTOR = '.product-card'

    @property
    def platform_name(self) -> str:
        return "medplus"

    def search_medicine(self, query: str) -> List[Dict]:
        return asyncio.run(self.search_async(query))

    async def search_async(self, query: str) -> List[Dict]:
        return await self.retry_search(lambda: self._search_once(query))

    async def _search_once(self, query: str) -> List[Dict]:
        # Medplus uses Base64 encoded search paths with A:: prefix
        try:
            search_query = f"A::{query}"
            encoded_query = base64.b64encode(search_query.encode()).decode()
            url = f"{self.BASE_URL}/searchAll/{encoded_query}"
        except Exception as e:
            logger.error(f"Error encoding query: {e}")
            url = f"{self.BASE_URL}/searchProduct?productName={query.replace(' ', '+')}"

        page = await self.get_page()
        try:
            logger.info(f"[{self.platform_name}] Navigating to {url}")
            # Wait for the product cards to render instead of a fixed 3s sleep.
            await self.goto_and_wait(page, url, wait_selector=self.PRODUCT_SELECTOR)
            html = await page.content()
        except Exception as e:
            logger.error(f"[{self.platform_name}] Navigation error: {e}")
            try:
                html = await page.content()
            except Exception:
                return []
        finally:
            await page.close()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')

        # Look for product cards
        cards = soup.select(self.PRODUCT_SELECTOR)
        if not cards:
            # Fallback to links if no cards
            cards = soup.select('a[href*="/product/"]')
        
        standardized_results = []
        seen_urls = set()
        
        for card in cards:
            try:
                # Find the product url from anchor tag
                link_tag = card if card.name == 'a' else card.select_one('a[href*="/product/"]')
                if not link_tag: continue
                product_url = link_tag.get('href')
                if not product_url: continue
                if not product_url.startswith('http'):
                    product_url = f"{self.BASE_URL}{product_url}"
                
                if product_url in seen_urls: continue
                seen_urls.add(product_url)
                
                # Check stock status
                card_text = card.get_text(separator=' | ', strip=True)
                in_stock = "Out of Stock" not in card_text
                
                # --- SMART NAME EXTRACTION ---
                # Get the product name (usually inside span or header, or inside link text)
                name = ""
                name_el = card.select_one('h6, h5, span[title]')
                if name_el:
                    name = name_el.get_text(strip=True)
                if not name:
                    # Fallback to parts of the card text
                    name_parts = card_text.split('|')
                    for part in name_parts:
                        part = part.strip()
                        if len(part) < 3: continue
                        # Ignore common manufacturer/action words
                        if part.isupper() and any(w in part for w in ['LIMITED', 'PVT', 'LTD', 'PHARMA', 'REMEDIES']):
                            continue
                        name = part
                        break
                if not name:
                    name = card_text[:60]
                
                # --- SMART PRICE EXTRACTION ---
                mrp = 0.0
                sale_price = 0.0
                
                # Strategy 1: Look for MRP label followed by price (robust to any intermediate characters)
                mrp_match = re.search(r'MRP\s*[^0-9\n]*\s*([\d,]+\.?\d*)', card_text, re.IGNORECASE)
                if mrp_match:
                    mrp = float(mrp_match.group(1).replace(',', ''))
                
                # Strategy 2: Look for discount rate to compute sale price (e.g. Save upto 16%)
                discount = 0.0
                discount_match = re.search(r'(?:Save\s+upto|Save)\s*[^0-9\n]*\s*(\d+)\s*%', card_text, re.IGNORECASE)
                if discount_match:
                    discount = float(discount_match.group(1))
                
                if mrp > 0:
                    if discount > 0:
                        sale_price = round(mrp * (1 - discount / 100.0), 2)
                    else:
                        sale_price = mrp
                
                # Strategy 3: Offer/Best price label
                if sale_price <= 0 or mrp <= 0:
                    sell_match = re.search(r'(?:Price|Offer|Best)\s*[^0-9\n]*\s*([\d,]+\.?\d*)', card_text, re.IGNORECASE)
                    if sell_match:
                        val = float(sell_match.group(1).replace(',', ''))
                        if sale_price <= 0: sale_price = val
                        if mrp <= 0: mrp = val
                
                # Strategy 4: Fallback to any rupee symbols
                if mrp <= 0 and sale_price <= 0:
                    rupee_matches = re.findall(r'[₹]\s*([\d,]+\.?\d*)', card_text)
                    if rupee_matches:
                        prices = [float(p.replace(',', '')) for p in rupee_matches]
                        if prices:
                            sale_price = min(prices)
                            mrp = max(prices)
                
                # Strategy 5: Last resort - find decimal numbers
                if mrp <= 0 and sale_price <= 0:
                    all_numbers = re.findall(r'(?<!\d)([\d]+\.[\d]{2})(?!\d)', card_text)
                    if all_numbers:
                        prices = [float(n) for n in all_numbers if float(n) < 500]
                        if prices:
                            sale_price = min(prices)
                            mrp = max(prices)
                
                if sale_price <= 0: continue
                if mrp < sale_price: mrp = sale_price
                
                # Parse manufacturer if present. Default "" (never the platform
                # name "Medplus") so a missing value doesn't masquerade as the
                # real maker and overwrite the detail JSON-LD value.
                manufacturer = ""
                mfg_el = card.select_one('p[class*="text-secondary"], p.text-muted')
                if mfg_el:
                    manufacturer = mfg_el.get_text(strip=True)
                
                standardized_results.append(self._standardize_result(
                    name=name, url=product_url, mrp=mrp, sale_price=sale_price,
                    pack_size="", manufacturer=manufacturer, in_stock=in_stock
                ))
            except Exception as item_err:
                logger.debug(f"Error parsing Medplus card item: {item_err}")
                continue
        return standardized_results

if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(level=logging.INFO)
    
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    scraper = MedplusScraper()
    
    print(f"Testing Medplus Scraper with Base64 Logic for: '{query}'")
    results = scraper.search_medicine(query)
    print(f"Found {len(results)} results.")
    for r in results[:3]:
        print(json.dumps(r, indent=2))
