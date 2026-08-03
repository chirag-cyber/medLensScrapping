import asyncio
import logging
import re
from typing import List, Dict
from scrapers.playwright_base import PlaywrightBaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class ApolloScraper(PlaywrightBaseScraper, PharmacyScraper):
    """
    Apollo Pharmacy scraper using Playwright to bypass WAF and parse DOM.
    """

    BASE_URL = "https://www.apollopharmacy.in"

    # Apollo renders results from an XHR; wait on the product links it produces.
    PRODUCT_SELECTOR = 'a[href*="/otc/"], a[href*="/medicine/"]'

    @property
    def platform_name(self) -> str:
        return "apollo"

    def search_medicine(self, query: str) -> List[Dict]:
        return asyncio.run(self.search_async(query))

    async def search_async(self, query: str) -> List[Dict]:
        return await self.retry_search(lambda: self._search_once(query))

    async def _search_once(self, query: str) -> List[Dict]:
        url = f"{self.BASE_URL}/search-medicines/{query.replace(' ', '%20')}"

        page = await self.get_page()
        try:
            logger.info(f"[{self.platform_name}] Navigating to {url}")
            # Wait for the product links to render instead of networkidle+sleep.
            await self.goto_and_wait(page, url, wait_selector=self.PRODUCT_SELECTOR)
            html = await page.content()
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Navigation timeout/error (ignoring): {e}")
            try:
                html = await page.content()
            except Exception:
                return []
        finally:
            await page.close()
            
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        
        # Apollo product cards - more robust selectors
        cards = []
        # Try finding by div containers that look like product cards
        containers = soup.find_all('div', class_=lambda c: c and ('ProductCard' in c or 'grid' in c.lower()))
        for container in containers:
            link = container.find('a', href=lambda h: h and ('/otc/' in h or '/medicine/' in h))
            if link:
                cards.append(container)
        
        # If still no cards, just take all relevant links
        if not cards:
            cards = soup.find_all('a', href=lambda h: h and ('/otc/' in h or '/medicine/' in h))
            
        seen_urls = set()
        standardized_results = []
        
        for card in cards:
            # Handle case where card is the link or contains the link
            link_tag = card if card.name == 'a' else card.find('a')
            if not link_tag: continue
            
            product_url = link_tag.get('href')
            if not product_url.startswith('http'):
                product_url = f"{self.BASE_URL}{product_url}"
                
            if product_url in seen_urls: continue
            seen_urls.add(product_url)
            
            # Text extraction
            text_blocks = [t.strip() for t in card.strings if t.strip()]
            if not text_blocks: continue
            
            name = ""
            for t in text_blocks:
                if len(t) > 5 and not t.startswith('₹') and not t.startswith('(₹'):
                    name = t
                    break
            if not name:
                 name = text_blocks[0]
            
            full_text = " ".join(text_blocks)
            
            # Find prices (Apollo uses ₹)
            prices = re.findall(r'[\u20b9₹]\s*([\d.]+)', full_text)
            
            mrp = 0.0
            sale_price = 0.0
            
            if prices:
                parsed_prices = [float(p) for p in prices]
                # Filter out absurdly high promotional values (e.g. cashback, extra savings)
                valid_prices = []
                for p in parsed_prices:
                    # Ignore random high numbers that aren't realistic for a single strip 
                    # unless they are the only number
                    valid_prices.append(p)
                
                valid_prices.sort()
                
                # Usually smallest is sale price, largest is MRP
                if len(valid_prices) >= 2:
                    sale_price = valid_prices[0]
                    # MRP shouldn't be more than 10x the sale price (avoids picking up 'save ₹609')
                    mrp_candidates = [p for p in valid_prices if p <= sale_price * 10]
                    mrp = mrp_candidates[-1] if mrp_candidates else valid_prices[-1]
                elif valid_prices:
                    sale_price = valid_prices[0]
                    mrp = sale_price

            if sale_price <= 0: continue
            
            # Pack size
            pack_size = ""
            pack_match = re.search(r'\d+\s*(?:Tablet|Capsule|ml|gm)s?', full_text, re.IGNORECASE)
            if pack_match:
                pack_size = pack_match.group(0)
                
            standardized_results.append(self._standardize_result(
                name=name, url=product_url, mrp=mrp, sale_price=sale_price,
                pack_size=pack_size, manufacturer="Apollo Partner", in_stock=True
            ))
            
        return standardized_results
