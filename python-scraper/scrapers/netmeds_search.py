import asyncio
import json
import logging
import re
from typing import List, Dict
from scrapers.playwright_base import PlaywrightBaseScraper
from scrapers.interface import (
    PharmacyScraper, text_in_stock, coerce_stock, clean_image_url,
    img_src_from_soup,
)

logger = logging.getLogger(__name__)

class NetmedsSearchScraper(PlaywrightBaseScraper, PharmacyScraper):
    """
    Netmeds search scraper.
    Strategy:
      1. Try __NEXT_DATA__ JSON extraction (Netmeds is a Next.js site)
      2. Fallback to DOM parsing with updated selectors
    """

    BASE_URL = "https://www.netmeds.com"

    # Client-rendered product cards; __NEXT_DATA__ is in the server HTML.
    PRODUCT_SELECTOR = '.product-card-container, .product-item, .catalogCard'

    @property
    def platform_name(self) -> str:
        return "netmeds"

    def search_medicine(self, query: str) -> List[Dict]:
        return asyncio.run(self.search_async(query))

    async def search_async(self, query: str) -> List[Dict]:
        return await self.retry_search(lambda: self._search_once(query))

    async def _search_once(self, query: str) -> List[Dict]:
        # Correct Netmeds search URL discovered via browser subagent
        url = f"{self.BASE_URL}/products?q={query.replace(' ', '%20')}"

        page = await self.get_page()
        try:
            logger.info(f"[{self.platform_name}] Navigating to {url}")
            # __NEXT_DATA__ is in the initial HTML; also give cards a chance to render.
            await self.goto_and_wait(page, url, wait_selector=self.PRODUCT_SELECTOR)

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
                await self.goto_and_wait(page, direct_url, wait_selector="h1")
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
                        # PDP hero image. Prefer the schema.org/og image the PDP
                        # declares, since the first <img> on a product page is
                        # often a header/logo rather than the pack shot.
                        og = soup.select_one(
                            'meta[property="og:image"], meta[name="og:image"]')
                        image_url = clean_image_url(
                            og.get('content') if og else '', self.BASE_URL)
                        if not image_url:
                            gallery = soup.select_one(
                                '.product-img, .pdp-img, [class*="productImage"]')
                            image_url = img_src_from_soup(
                                gallery or soup, self.BASE_URL)

                        results.append(self._standardize_result(
                            name=name, url=direct_url, mrp=price, sale_price=price,
                            pack_size="", manufacturer="",
                            # PDP fallback: the page body carries an OOS banner
                            # ("Out of Stock"/"Notify Me") when unavailable.
                            in_stock=text_in_stock(soup.get_text(" ", strip=True)),
                            image_url=image_url
                        ))
                    except: pass

            return results
        finally:
            await page.close()

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

                # Real availability from the SSR payload where present (Magento
                # commonly exposes is_in_stock / stock_status); defaults to True
                # only when no stock field exists.
                in_stock = coerce_stock(
                    item.get('is_in_stock',
                             item.get('in_stock',
                                      item.get('available',
                                               item.get('stock_status')))))

                # Netmeds' SSR payload names this field differently per template;
                # take whichever is present. `image_url` resolves relative paths
                # against BASE_URL, so a bare "/assets/..." still works.
                image_url = (item.get('image') or item.get('imageUrl')
                             or item.get('image_url') or item.get('thumbnail') or '')
                if isinstance(image_url, dict):
                    image_url = image_url.get('url') or image_url.get('src') or ''
                if isinstance(image_url, list) and image_url:
                    first = image_url[0]
                    image_url = first.get('url', '') if isinstance(first, dict) else first

                standardized_results.append(self._standardize_result(
                    name=name, url=product_url, mrp=mrp, sale_price=sale_price,
                    pack_size=str(pack_size), manufacturer=manufacturer,
                    in_stock=in_stock, image_url=image_url
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
                    pack_size=pack_size, manufacturer=manufacturer,
                    in_stock=text_in_stock(item.get_text(" ", strip=True)),
                    # Card thumbnail. "" when the tile has no <img> yet — the UI
                    # falls back to the Netmeds logo rather than another
                    # pharmacy's photo.
                    image_url=img_src_from_soup(item, self.BASE_URL)
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
