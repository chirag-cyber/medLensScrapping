import logging
import requests
from typing import List, Dict
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class PlatinumRxScraper(PharmacyScraper):
    """
    PlatinumRx scraper using their internal API for maximum speed and data richness.
    Bypasses Playwright entirely for this platform.
    """

    BASE_URL = "https://www.platinumrx.in"
    API_URL = "https://backend.platinumrx.in/pdp/fetchPlpInfo"

    def __init__(self, delay: float = 0):
        self.delay = delay

    @property
    def platform_name(self) -> str:
        return "platinumrx"

    def search_medicine(self, query: str) -> List[Dict]:
        """Fetch results directly from PlatinumRx API."""
        payload = {
            "drugName": query,
            "searchType": None
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://www.platinumrx.in",
            "referer": "https://www.platinumrx.in/"
        }

        try:
            logger.info(f"[{self.platform_name}] Fetching API for: {query}")
            response = requests.post(self.API_URL, json=payload, headers=headers, timeout=15)
            
            if response.status_code != 200:
                logger.error(f"[{self.platform_name}] API error: {response.status_code}")
                return []

            data = response.json()
            items = data.get("message", [])
            
            standardized_results = []
            for item in items:
                master = item.get("masterItemData", {})
                if not master: continue

                name = master.get("display_name", "Unknown")
                drug_id = master.get("master_drug_code", "")
                
                # Construct URL (same as the GitHub script)
                import urllib.parse
                encoded_name = urllib.parse.quote(name)
                product_url = f"{self.BASE_URL}/medicines/{encoded_name}/{drug_id}"
                
                mrp = float(master.get("mrp", 0))
                sale_price = float(master.get("discounted_price", mrp))
                
                res = self._standardize_result(
                    name=name,
                    url=product_url,
                    mrp=mrp,
                    sale_price=sale_price,
                    pack_size=f"{master.get('pack_quantity_value')} {master.get('unit_of_measurement')}",
                    manufacturer=master.get("manufacturer_name", "PlatinumRx"),
                    in_stock=bool(master.get("drug_stock", 1))
                )
                
                # Add salt composition if available
                if master.get("salt_composition"):
                    res['composition'] = master.get("salt_composition")
                
                standardized_results.append(res)
            
            return standardized_results

        except Exception as e:
            logger.error(f"[{self.platform_name}] API Error: {e}")
            return []

    async def search_async(self, query: str) -> List[Dict]:
        """Async wrapper for the API call."""
        import asyncio
        return await asyncio.to_thread(self.search_medicine, query)

    def close(self):
        pass
