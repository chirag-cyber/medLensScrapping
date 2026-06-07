import asyncio
import json
import logging
import re
from typing import List, Dict
from scrapers.playwright_base import PlaywrightBaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class NetmedsSearchScraper(PlaywrightBaseScraper, PharmacyScraper):
    """
    Netmeds search scraper.
    Strategy:
      1. Try __NEXT_DATA__ JSON extraction (Netmeds is a Next.js site)
      2. Fallback to DOM parsing with updated selectors
    """

    BASE_URL = "https://www.netmeds.com"

    @property
    def platform_name(self) -> str:
        return "netmeds"

    async def get_page(self):
        """Override to use desktop user agent for Netmeds."""
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
        # Correct Netmeds search URL discovered via browser subagent
        url = f"{self.BASE_URL}/products?q={query.replace(' ', '%20')}"
        
        page = await self.get_page()
        try:
            logger.info(f"[{self.platform_name}] Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(4.0 + self.delay)
            
            # --- Strategy 1: Try __NEXT_DATA__ extraction ---
            next_data = await page.evaluate('() => document.getElementById("__NEXT_DATA__")?.textContent')
            if next_data:
                results = self._parse_next_data(next_data)
                if results:
                    logger.info(f"[{self.platform_name}] Extracted {len(results)} results from __NEXT_DATA__")
                    return results
            
            # --- Strategy 2: DOM parsing fallback ---
            html = await page.content()
            results = self._parse_dom(html)
            
            if not results:
                # --- Strategy 3: Direct URL Fallback (from user script) ---
                slug = query.lower().replace(' ', '-').replace("'", '-').replace("\\", "-").replace(".", "-").replace("%", "")
                direct_url = f"{self.BASE_URL}/prescriptions/{slug}"
                logger.info(f"[{self.platform_name}] Search yielded 0 results, trying direct URL: {direct_url}")
                await page.goto(direct_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                direct_html = await page.content()
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(direct_html, 'lxml')
                
                name_tag = soup.select_one('h1.black-txt') or soup.find('h1')
                price_tag = soup.find("span", {"class": "final-price"})
                if name_tag and price_tag:
                    name = name_tag.get_text(strip=True)
                    price_str = price_tag.get_text(strip=True).replace('M.R.P.: Rs.', '').replace('Rs.', '').replace(',', '').strip()
                    try:
                        price = float(price_str)
                        results.append(self._standardize_result(
                            name=name, url=direct_url, mrp=price, sale_price=price,
                            pack_size="", manufacturer="", in_stock=True
                        ))
                    except: pass
                    
            return results
        except Exception as e:
            logger.error(f"[{self.platform_name}] Error: {e}")
            return []
        finally:
            await page.context.close()

    def _parse_next_data(self, next_data_str: str) -> List[Dict]:
        """Parse __NEXT_DATA__ for Netmeds search results."""
        try:
            data = json.loads(next_data_str)
            page_props = data.get('props', {}).get('pageProps', {})
            
            # Try common Netmeds paths
            products = (
                page_props.get('productList', []) or
                page_props.get('searchData', {}).get('products', []) or
                page_props.get('products', []) or
                page_props.get('initialProps', {}).get('products', [])
            )
            
            standardized_results = []
            for item in products:
                name = item.get('name') or item.get('productName') or item.get('display_name', '')
                if not name: continue
                
                slug = item.get('slug') or item.get('url_key', '') or item.get('urlKey', '')
                product_url = f"{self.BASE_URL}/{slug}" if slug and not slug.startswith('http') else slug
                if not product_url: continue
                
                mrp = float(item.get('mrp', 0) or item.get('price', 0) or 0)
                sale_price = float(item.get('best_price', 0) or item.get('final_price', 0) or item.get('sellingPrice', 0) or mrp)
                if sale_price <= 0: continue
                
                pack_size = item.get('pack_size_label', '') or item.get('packSize', '')
                manufacturer = item.get('manufacturer', '') or item.get('brand', '')
                
                standardized_results.append(self._standardize_result(
                    name=name, url=product_url, mrp=mrp, sale_price=sale_price,
                    pack_size=str(pack_size), manufacturer=manufacturer, in_stock=True
                ))
            
            return standardized_results
        except Exception as e:
            logger.debug(f"[{self.platform_name}] __NEXT_DATA__ parse failed: {e}")
            return []

    def _parse_dom(self, html: str) -> List[Dict]:
        """Fallback DOM parsing with multiple selector strategies."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        
        # New selectors discovered via Netmeds debug page
        items = soup.select('.product-card-container, .product-item, .catalogCard')
        
        standardized_results = []
        for item in items:
            try:
                # Name
                name_tag = item.select_one('h3, h4, .title, .product-name')
                if not name_tag:
                    continue
                name = name_tag.get_text(strip=True)
                if not name or len(name) < 3: continue
                
                # URL
                link_tag = item.select_one('a[href*="/product/"], a[href*="/prescriptions/"]')
                if not link_tag: continue
                product_url = link_tag.get('href', '')
                if not product_url: continue
                if not product_url.startswith('http'):
                    product_url = f"{self.BASE_URL}{product_url}"
                
                # Price parsing
                sale_price = 0.0
                mrp = 0.0
                
                price_tag = item.select_one('.priceDisplay, .final-price')
                if price_tag:
                    p_str = re.sub(r'[^\d.]', '', price_tag.get_text(strip=True))
                    if p_str: sale_price = float(p_str)
                
                # If there's an MRP, parse it (Netmeds crossed price usually has class mrp or similar)
                mrp_tag = item.select_one('.mrp, [class*="mrp"], [class*="Mrp"]')
                if mrp_tag:
                    m_str = re.sub(r'[^\d.]', '', mrp_tag.get_text(strip=True))
                    if m_str: mrp = float(m_str)
                
                if mrp <= 0: mrp = sale_price
                if sale_price <= 0: continue
                if mrp < sale_price: mrp = sale_price
                
                # Pack size
                pack_size = ""
                pack_el = item.select_one('div[class*="body-xxxs"], .drug-varients, [class*="packSize"]')
                if pack_el: pack_size = pack_el.get_text(strip=True)
                
                # Manufacturer
                manufacturer = ""
                mfg_el = item.select_one('.manufacturer-title, .drug-manu, [class*="manufacturer"]')
                if mfg_el: 
                    manufacturer = mfg_el.get_text(strip=True).replace('By', '').strip()
                
                standardized_results.append(self._standardize_result(
                    name=name, url=product_url, mrp=mrp, sale_price=sale_price,
                    pack_size=pack_size, manufacturer=manufacturer, in_stock=True
                ))
            except Exception as e:
                logger.debug(f"Error parsing item: {e}")
                continue
        return standardized_results

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    scraper = NetmedsSearchScraper()
    
    print(f"Testing Netmeds Scraper for: '{query}'")
    results = scraper.search_medicine(query)
    print(f"Found {len(results)} results.")
    for r in results[:3]:
        print(json.dumps(r, indent=2))
