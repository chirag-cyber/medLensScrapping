import re
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

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
        
        html = self.get(url)
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

            link_tag = name_tag.find('a')
            if link_tag and link_tag.get('href'):
                product_url = f"{self.BASE_URL}{link_tag.get('href')}"
            else:
                continue

            price_tag = item.find('span', class_='a-price-whole')
            mrp_tag = item.find('span', class_='a-text-price')
            
            sale_price = 0.0
            if price_tag:
                price_str = price_tag.get_text(strip=True).replace(',', '').replace('.', '')
                try:
                    sale_price = float(price_str)
                except ValueError:
                    pass

            mrp = sale_price
            if mrp_tag:
                offscreen = mrp_tag.find('span', class_='a-offscreen')
                if offscreen:
                    mrp_str = offscreen.get_text(strip=True).replace('₹', '').replace(',', '')
                    try:
                        mrp = float(mrp_str)
                    except ValueError:
                        pass
            
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
                manufacturer="Amazon Seller", # Hard to extract from search
                in_stock=True
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
