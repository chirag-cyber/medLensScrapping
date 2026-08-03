import json
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class PharmEasyScraper(BaseScraper, PharmacyScraper):
    """
    PharmEasy automated scraper.
    Uses __NEXT_DATA__ from server-side rendered search pages.
    """

    BASE_URL = "https://pharmeasy.in"

    @property
    def platform_name(self) -> str:
        return "pharmeasy"

    def search_medicine(self, query: str) -> List[Dict]:
        """Synchronous search wrapper."""
        import asyncio
        return asyncio.run(self.search_async(query))

    async def search_async(self, query: str) -> List[Dict]:
        """The main async search logic for PharmEasy."""
        url = f"{self.BASE_URL}/search/all?name={query.replace(' ', '+')}"
        html = self.get(url)
        
        if not html:
            logger.error(f"[PharmEasy] Failed to fetch search page for query: {query}")
            return []

        soup = BeautifulSoup(html, 'lxml')
        next_data_script = soup.find('script', id='__NEXT_DATA__')
        
        if not next_data_script or not next_data_script.string:
            logger.error(f"[PharmEasy] __NEXT_DATA__ not found for query: {query}")
            return []

        try:
            data = json.loads(next_data_script.string)
            page_props = data.get('props', {}).get('pageProps', {})
            # PharmEasy's SSR payload moved the product array from `searchResults`
            # to `productList` (with a parallel `genericsProductList` for
            # generic substitutes). Read both so we recover full coverage.
            results = (page_props.get('productList', []) or []) + \
                      (page_props.get('genericsProductList', []) or [])

            standardized_results = []
            for item in results:
                # Some items might be categories or generic pages, filter for products
                # In PharmEasy __NEXT_DATA__, entityType 2 means product
                if item.get('entityType') != 2 and item.get('entityType') != 'PRODUCT':
                    continue

                name = item.get('name', '')
                slug = item.get('slug', '')
                if not name or not slug:
                    continue

                product_url = f"{self.BASE_URL}/online-medicine-order/{slug}"
                mrp = float(item.get('mrpDecimal', 0.0))
                sale_price = float(item.get('salePriceDecimal', item.get('price', mrp)))
                pack_size = item.get('measurementUnit', item.get('subtitleText', ''))
                manufacturer = item.get('manufacturer', '')
                
                # Check availability flag
                flags = item.get('productAvailabilityFlags', {})
                in_stock = flags.get('isAvailable', True)

                res = self._standardize_result(
                    name=name,
                    url=product_url,
                    mrp=mrp,
                    sale_price=sale_price,
                    pack_size=pack_size,
                    manufacturer=manufacturer,
                    in_stock=in_stock
                )

                # PharmEasy exposes the salt as `moleculeName` — attach it as
                # composition (same optional key truemeds/platinumrx set) so
                # salt validation gets this for free.
                molecule = item.get('moleculeName', '')
                if molecule:
                    res['composition'] = molecule

                standardized_results.append(res)

            return standardized_results

        except json.JSONDecodeError:
            logger.error(f"[PharmEasy] Failed to parse __NEXT_DATA__ JSON for query: {query}")
            return []
        except Exception as e:
            logger.error(f"[PharmEasy] Unexpected error parsing search results: {e}")
            return []

if __name__ == "__main__":
    # Test script
    import sys
    logging.basicConfig(level=logging.INFO)
    
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    
    with PharmEasyScraper(delay=0.5) as scraper:
        print(f"Searching PharmEasy for: {query}")
        results = scraper.search_medicine(query)
        print(f"Found {len(results)} results.")
        for r in results[:3]:
            print(json.dumps(r, indent=2))
