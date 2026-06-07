from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class PharmacyScraper(ABC):
    """
    Base interface for all pharmacy scrapers.
    Standardizes the output format across all platforms.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the standardized name of the platform (e.g., '1mg', 'pharmeasy')."""
        pass

    @abstractmethod
    def search_medicine(self, query: str) -> List[Dict]:
        """
        Search for a medicine and return standardized pricing results.
        """
        pass

    @abstractmethod
    async def search_async(self, query: str) -> List[Dict]:
        """
        Asynchronously search for a medicine.
        """
        pass

    def _standardize_result(self, 
                            name: str, 
                            url: str, 
                            mrp: float, 
                            sale_price: float, 
                            pack_size: str = "", 
                            manufacturer: str = "", 
                            in_stock: bool = True) -> Dict:
        """Helper to create a consistently formatted result dictionary."""
        
        # Calculate discount if not provided but we have MRP and Sale Price
        discount_percent = 0.0
        if mrp and sale_price and mrp > sale_price:
            discount_percent = round(((mrp - sale_price) / mrp) * 100, 2)
            
        return {
            "platform": self.platform_name,
            "name": name.strip() if name else "",
            "url": url.strip() if url else "",
            "mrp": float(mrp) if mrp else 0.0,
            "sale_price": float(sale_price) if sale_price else 0.0,
            "discount_percent": float(discount_percent),
            "pack_size": pack_size.strip() if pack_size else "",
            "manufacturer": manufacturer.strip() if manufacturer else "",
            "in_stock": bool(in_stock)
        }
