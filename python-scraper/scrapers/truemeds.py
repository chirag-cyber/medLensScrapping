import logging
import requests
import asyncio
from typing import List, Dict
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class TruemedsScraper(PharmacyScraper):
    """
    Truemeds scraper using the discovered secret API (nal.tmmumbai.in).
    This is extremely reliable and bypasses all browser-related blocks.
    """

    BASE_URL = "https://www.truemeds.in"
    API_URL = "https://nal.tmmumbai.in/CustomerService/getSearchResult"

    def __init__(self, delay: float = 0):
        self.delay = delay

    @property
    def platform_name(self) -> str:
        return "truemeds"

    def search_medicine(self, query: str) -> List[Dict]:
        """Fetch search results directly from Truemeds Secret API."""
        params = {
            "warehouseId": "20",
            "elasticSearchType": "SKU_BRAND_SEARCH",
            "searchString": query,
            "isMultiSearch": "true",
            "pageName": "srp",
            "variantId": "18",
            "platform": "m_web"
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.truemeds.in",
            "referer": "https://www.truemeds.in/",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1"
        }

        try:
            logger.info(f"[{self.platform_name}] Fetching Secret API for: {query}")
            response = requests.get(self.API_URL, headers=headers, params=params, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"[{self.platform_name}] API failed with status {response.status_code}")
                return []

            data = response.json()
            products_list = data.get("responseData", {}).get("elasticProductDetails", [])
            
            standardized_results = []
            for item in products_list:
                master = item.get("product", {})
                if not master: continue

                name = master.get("skuName", "Unknown")
                url_slug = master.get("productUrlSuffix", "")
                product_url = f"{self.BASE_URL}/{url_slug}" if url_slug else f"{self.BASE_URL}/search?q={query}"
                
                mrp = float(master.get("mrp", 0))
                sale_price = float(master.get("sellingPrice", mrp))
                
                res = self._standardize_result(
                    name=name,
                    url=product_url,
                    mrp=mrp,
                    sale_price=sale_price,
                    pack_size=master.get("packForm", ""),
                    manufacturer=master.get("manufacturerName", "Truemeds"),
                    in_stock=True # API usually returns active products
                )
                
                # Add composition (salt) if available
                if master.get("composition"):
                    res['composition'] = master.get("composition")
                    
                standardized_results.append(res)
            
            return standardized_results
        except Exception as e:
            logger.error(f"[{self.platform_name}] Secret API Error: {e}")
            return []

    async def search_async(self, query: str) -> List[Dict]:
        """Async wrapper for the secret API call."""
        return await asyncio.to_thread(self.search_medicine, query)

    def close(self):
        pass
