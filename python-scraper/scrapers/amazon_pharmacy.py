import re
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from scrapers.interface import PharmacyScraper, text_in_stock, img_src_from_soup

logger = logging.getLogger(__name__)


def _parse_price(text: str) -> float:
    """Parse an Amazon price string ('₹1,234.00', '1,234.', '45.50') to a float.

    Strips the currency symbol and thousands separators but PRESERVES the decimal
    point. The old code did .replace('.', ''), which turned a full-precision
    '1234.00' into '123400' — a 100x price inflation whenever Amazon rendered the
    whole rupee amount with its fraction in one span. A lone trailing dot (from
    Amazon's nested `a-price-decimal` span, e.g. '1,234.') is dropped."""
    if not text:
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', text).rstrip('.')
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


class AmazonPharmacyScraper(BaseScraper, PharmacyScraper):
    """
    Amazon Pharmacy automated scraper.
    Parses HTML search results using standard Amazon selectors.
    """

    BASE_URL = "https://www.amazon.in"

    @property
    def platform_name(self) -> str:
        return "amazon_pharmacy"

    async def search_async(self, query: str) -> List[Dict]:
        """Async search for Amazon Pharmacy."""
        import asyncio
        return await asyncio.to_thread(self.search_medicine, query)

    def search_medicine(self, query: str) -> List[Dict]:
        """Search Amazon Pharmacy."""
        # Note: Amazon Pharmacy search adds 'pharmacy' or 'medicine' keywords
        search_query = f"{query} tablet".replace(' ', '+')
        url = f"{self.BASE_URL}/s?k={search_query}"
        
        # Amazon is strict about headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.amazon.in/'
        }
        
        # Use underlying session directly to set custom headers if needed
        self.session.headers.update(headers)

        # Amazon intermittently 503s on bot heuristics (~1 in 8 requests). Each
        # self.get() rotates the User-Agent, so a fresh attempt almost always
        # recovers — retry a few times on empty before giving up.
        html = None
        for attempt in range(3):
            html = self.get(url)
            if html:
                break
            logger.warning(f"[Amazon] Empty/blocked response (attempt {attempt + 1}/3) for '{query}', retrying...")
        if not html:
            logger.error(f"[Amazon] Failed to fetch search page for query: {query}")
            return []

        soup = BeautifulSoup(html, 'lxml')
        results = soup.find_all('div', attrs={'data-component-type': 's-search-result'})
        
        standardized_results = []
        for item in results:
            name_tag = item.find('h2')
            if not name_tag:
                continue
            
            name = name_tag.get_text(strip=True)
            
            # Skip non-medicines roughly
            if 'tablet' not in name.lower() and 'syrup' not in name.lower() and 'capsule' not in name.lower():
                # Just a rough heuristic, we can adjust later
                pass

            # Amazon now renders the title <h2> INSIDE the product <a> (the
            # anchor is the parent, not a child). Resolve the link whether it's
            # a child or parent of the h2, then fall back to the tile's first
            # product anchor.
            link_tag = name_tag.find('a') or name_tag.find_parent('a', class_='a-link-normal')
            if not (link_tag and link_tag.get('href')):
                link_tag = item.find('a', class_='a-link-normal', href=True)
            if not (link_tag and link_tag.get('href')):
                continue
            product_url = f"{self.BASE_URL}{link_tag.get('href')}"

            # Price: prefer the `a-offscreen` span, which always carries the
            # clean, layout-independent full price ('₹1,234.00'). Fall back to the
            # visible `a-price-whole` split-span only if offscreen is absent.
            price_span = item.find('span', class_='a-price')
            sale_price = 0.0
            if price_span:
                off = price_span.find('span', class_='a-offscreen')
                if off:
                    sale_price = _parse_price(off.get_text(strip=True))
                else:
                    whole = price_span.find('span', class_='a-price-whole')
                    if whole:
                        sale_price = _parse_price(whole.get_text(strip=True))
            if sale_price == 0.0:
                # Legacy layout: bare a-price-whole not wrapped in a-price.
                legacy = item.find('span', class_='a-price-whole')
                if legacy:
                    sale_price = _parse_price(legacy.get_text(strip=True))

            mrp = sale_price
            mrp_tag = item.find('span', class_='a-text-price')
            if mrp_tag:
                offscreen = mrp_tag.find('span', class_='a-offscreen')
                if offscreen:
                    parsed_mrp = _parse_price(offscreen.get_text(strip=True))
                    if parsed_mrp > 0:
                        mrp = parsed_mrp

            if sale_price == 0:
                # No price available
                continue

            # Amazon usually doesn't show pack size distinct from name easily, so extract from name
            pack_size = ""
            pack_match = re.search(r'(?:strip|pack)\s+of\s+\d+|(\d+\s*(?:tablets|capsules|ml))', name, re.IGNORECASE)
            if pack_match:
                pack_size = pack_match.group(0).strip()

            standardized_results.append(self._standardize_result(
                name=name,
                url=product_url,
                mrp=mrp,
                sale_price=sale_price,
                pack_size=pack_size,
                # "Amazon Seller" is a marketplace label, not the real maker, and
                # can't be extracted from the search tile. Emit "" so it never
                # overwrites the true manufacturer from the detail JSON-LD.
                manufacturer="",
                # OOS listings show "Currently unavailable"/"Out of stock" in the
                # result tile; read the whole tile's text to detect it.
                in_stock=text_in_stock(item.get_text(" ", strip=True)),
                # `s-image` is Amazon's product thumbnail; scope to it so a Prime
                # or sponsored badge <img> in the tile can't win. Falls back to
                # the tile's first <img> when that class is absent.
                image_url=img_src_from_soup(
                    item.find('img', class_='s-image') or item, self.BASE_URL)
            ))
            
        return standardized_results

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    
    with AmazonPharmacyScraper(delay=1.0) as scraper:
        print(f"Searching Amazon for: {query}")
        results = scraper.search_medicine(query)
        print(f"Found {len(results)} results.")
        for r in results[:3]:
            import json
            print(json.dumps(r, indent=2))
