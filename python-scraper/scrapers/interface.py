from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import re

# Phrases that mean a product tile/page is not purchasable right now. Shared by
# every HTML/browser scraper so out-of-stock detection is consistent instead of
# each platform hardcoding in_stock=True. Kept deliberately specific so a normal
# product description ("in stock", "available") is never mistaken for OOS.
_OOS_PATTERN = re.compile(
    r'out of stock|sold\s*out|notify me|currently unavailable|'
    r'not available|unavailable|temporarily out',
    re.IGNORECASE,
)


def text_in_stock(text: str) -> bool:
    """Return False when the given card/page text signals out-of-stock.

    Defaults to True (in stock) for empty/absent text — absence of an OOS marker
    is treated as available, which matches how these storefronts render the
    common case (only OOS items carry an explicit banner)."""
    if not text:
        return True
    return not _OOS_PATTERN.search(text)


def coerce_stock(value, default: bool = True) -> bool:
    """Coerce a heterogeneous stock field into a boolean.

    Handles the shapes different APIs use: a real bool, a 0/1 flag, or a status
    string ("IN_STOCK" / "Out of Stock"). None/unknown falls back to `default`
    (True), so a platform that simply doesn't expose stock is treated as
    available rather than silently hidden."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return text_in_stock(value.replace('_', ' '))
    return default


# Placeholders these storefronts serve while the real photo lazy-loads. Storing
# one would show every pharmacy the same grey square and look like a bug.
_IMG_PLACEHOLDER = re.compile(
    r'data:image|/placeholder|placeholder\.|no[-_]?image|default[-_]?(product|image)|'
    r'blank\.(gif|png)|spacer\.(gif|png)|loading\.(gif|svg)',
    re.IGNORECASE,
)


def clean_image_url(value, base: str = "") -> str:
    """Normalise a scraped product image URL, or return "" if unusable.

    Protocol-relative and root-relative sources are resolved against `base` so
    the stored URL works from a browser on our domain. Anything that is not an
    absolute http(s) URL after that, or that matches a known lazy-load
    placeholder, is dropped — an empty string means "this platform has no image"
    and the UI falls back to the platform logo.
    """
    if not value or not isinstance(value, str):
        return ""
    url = value.strip()
    if not url:
        return ""
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('/') and base:
        url = base.rstrip('/') + url
    if not url.lower().startswith(('http://', 'https://')):
        return ""
    if _IMG_PLACEHOLDER.search(url):
        return ""
    return url


# Attributes that can hold the real source on a lazy-loaded <img>. `src` is last
# on purpose: while the image is still lazy it holds the placeholder, and the
# true URL sits in one of the data-* attributes.
_IMG_ATTRS = ('data-src', 'data-original', 'data-lazy-src', 'data-image', 'src')


def img_src_from_soup(node, base: str = "") -> str:
    """Pull the best product image URL out of a BeautifulSoup card/img node.

    Accepts either an <img> tag or a container to search within. Reads the
    lazy-load data attributes before `src`, and picks the widest candidate from
    `srcset` when present. Returns "" when nothing usable is found — callers
    must not substitute another platform's image.
    """
    if node is None:
        return ""
    img = node if getattr(node, 'name', '') == 'img' else node.find('img')
    if img is None:
        return ""

    for attr in _IMG_ATTRS:
        cleaned = clean_image_url(img.get(attr), base)
        if cleaned:
            return cleaned

    # srcset: "url 80w, url 320w" — take the largest declared width.
    srcset = img.get('srcset') or img.get('data-srcset') or ''
    best_url, best_w = "", -1.0
    for part in srcset.split(','):
        bits = part.strip().split()
        if not bits:
            continue
        cleaned = clean_image_url(bits[0], base)
        if not cleaned:
            continue
        width = 0.0
        if len(bits) > 1:
            try:
                width = float(re.sub(r'[^\d.]', '', bits[1]) or 0)
            except ValueError:
                width = 0.0
        if width > best_w:
            best_url, best_w = cleaned, width
    return best_url


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
                            in_stock: bool = True,
                            image_url: str = "",
                            image_base: str = "") -> Dict:
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
            "in_stock": bool(in_stock),
            # Per-platform product photo. Empty string when this platform did not
            # expose one — the UI falls back to the platform logo. Never borrow
            # another pharmacy's photo: it can be a different pack or strength.
            "image_url": clean_image_url(image_url, image_base or getattr(self, "BASE_URL", "")),
        }
