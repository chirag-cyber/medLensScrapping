import logging
import asyncio
from typing import List, Dict
from scrapers.base import BaseScraper
from scrapers.interface import PharmacyScraper

logger = logging.getLogger(__name__)

class TruemedsScraper(BaseScraper, PharmacyScraper):
    """
    Truemeds scraper using the discovered secret API (nal.tmmumbai.in).
    This is extremely reliable and bypasses all browser-related blocks.
    """

    BASE_URL = "https://www.truemeds.in"
    API_URL = "https://nal.tmmumbai.in/CustomerService/getSearchResult"

    def __init__(self, delay: float = 0):
        # Route through the resilient session (retry/backoff/pooling/rate-limit).
        super().__init__(delay=delay)

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
            "origin": "https://www.truemeds.in",
            "referer": "https://www.truemeds.in/",
        }

        try:
            logger.info(f"[{self.platform_name}] Fetching Secret API for: {query}")
            data = self.request_json(self.API_URL, method="GET", params=params, extra_headers=headers)
            if not data:
                return []

            products_list = data.get("responseData", {}).get("elasticProductDetails", [])
            
            standardized_results = []
            for item in products_list:
                # Per-item guard: a single malformed record must not sink the
                # whole platform's results.
                try:
                    master = item.get("product", {})
                    if not master: continue

                    name = master.get("skuName", "Unknown")
                    url_slug = master.get("productUrlSuffix", "")
                    product_url = f"{self.BASE_URL}/{url_slug}" if url_slug else f"{self.BASE_URL}/search?q={query}"

                    mrp = float(master.get("mrp", 0) or 0)
                    sale_price = float(master.get("sellingPrice", mrp) or mrp)

                    res = self._standardize_result(
                        name=name,
                        url=product_url,
                        mrp=mrp,
                        sale_price=sale_price,
                        pack_size=master.get("packForm", ""),
                        # Real maker from the API when present; fall back to ""
                        # (never the platform name) so a missing value doesn't
                        # masquerade as the manufacturer.
                        manufacturer=master.get("manufacturerName") or "",
                        # Real availability from the API: `available` is a bool and
                        # `availabilityStatus` reads e.g. "Out of Stock". Many search
                        # hits are genuinely OOS, so this must not be hardcoded True.
                        in_stock=bool(master.get("available", True))
                    )

                    # Add composition (salt) if available
                    if master.get("composition"):
                        res['composition'] = master.get("composition")

                    standardized_results.append(res)
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(
                        f"[{self.platform_name}] Skipping malformed item: {e}")
                    continue

            return standardized_results
        except Exception as e:
            logger.error(f"[{self.platform_name}] Secret API Error: {e}")
            return []

    async def search_async(self, query: str) -> List[Dict]:
        """Async wrapper for the secret API call."""
        return await asyncio.to_thread(self.search_medicine, query)
