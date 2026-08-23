"""
pdp_fetch.py — Direct product-page (PDP) fetch fallback.

WHY THIS EXISTS
A platform's own site-search often refuses to surface a low-visibility brand at
all: searching "Pactol 650" (or even bare "Pactol") on netmeds/truemeds/apollo
returns same-salt substitutes (Crocin/Dolo/Calpol) and never the exact product,
so that platform contributes zero rows to the price grid even though the product
IS listed and purchasable. Confirmed live for 6 of 8 platforms.

Search-side fixes cannot recover those platforms — the ranking, not the parser,
is what drops the brand. But the product page itself fetches and parses fine
over plain HTTP. So when search misses a platform AND we already know that
platform's product URL (stored from a previous sync, or seeded), we fetch the
PDP directly instead.

Product URLs carry unguessable IDs (`...-8291574`, `...-tm-tacr1-089944`), so a
URL can never be constructed from a medicine name — this module only ever
fetches a URL it was GIVEN. No guessing, no crawling.

Extraction is schema-first (schema.org Product JSON-LD, which every one of these
storefronts emits for SEO) with a `__NEXT_DATA__` fallback. That keeps one
parser working across platforms instead of six bespoke ones, and JSON-LD is far
more stable than CSS class names.

Pure fallback: nothing here runs unless search already missed the platform, so
it cannot change any result search already produces.
"""

import html as html_lib
import json
import logging
import re
from typing import Dict, Optional

from urllib.parse import urlsplit

from scrapers.base import BaseScraper
from scrapers.interface import coerce_stock, clean_image_url

logger = logging.getLogger(__name__)

_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_NEXT_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# schema.org availability -> in_stock. Anything containing "InStock" is available;
# OutOfStock/SoldOut/Discontinued are not. Absent availability defaults to True
# (matches interface.coerce_stock's "no OOS marker means available" convention).
_OOS_AVAIL = ("outofstock", "soldout", "discontinued", "outofservice")


def _unescape(text: str) -> str:
    """Decode HTML entities in scraped names ("PACTOL 650mg Tablet 10&#39;s")."""
    return html_lib.unescape(text or "")


def _page_in_stock(html: str) -> bool:
    """Stock from page markup when no structured availability field exists.

    A PDP mentions "out of stock" in places that say nothing about THIS product
    (the substitutes carousel, "notify me" copy for related items), so a whole-page
    text scan produces false OOS — it marked an in-stock 1mg product unavailable.
    Instead trust only an explicit machine-readable availability marker, and
    default to in-stock when none is present (same convention as
    interface.text_in_stock: only an explicit signal means unavailable).
    """
    m = re.search(
        r'"(?:available|in_stock|inStock|is_available)"\s*:\s*(true|false)', html)
    if m:
        return m.group(1) == "true"
    m = re.search(r'schema\.org/(InStock|OutOfStock|SoldOut)', html, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "instock"
    return True


def _num(value) -> float:
    """Best-effort float from '13.5', '₹13.50', 13.5, None."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r'([\d]+\.?[\d]*)', str(value).replace(',', ''))
    return float(m.group(1)) if m else 0.0


def _iter_ld_objects(html: str):
    """Yield every dict inside every JSON-LD block (handles @graph and arrays)."""
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node


_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


def _ld_image(value) -> str:
    """Flatten schema.org `image` into a single URL string.

    The spec allows a bare URL, an array of URLs, or ImageObject dicts — every
    storefront here uses a different one. Returns "" for anything else; the
    caller treats that as "this platform has no image" rather than substituting.
    """
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or ""
    return value if isinstance(value, str) else ""


def _og_image(html: str) -> str:
    """Read the page's og:image, the one image tag every storefront emits."""
    m = _OG_IMAGE_RE.search(html)
    if not m:
        return ""
    return _unescape(m.group(1) or m.group(2) or "").strip()


def _extract_from_ld(html: str) -> Optional[Dict]:
    """Pull name/price/mrp/availability from schema.org Product JSON-LD.

    Accepts `Drug` as well as `Product`: pharmacy storefronts legitimately mark a
    medicine page up as schema.org/Drug (netmeds does), and it carries the same
    `offers` block. Restricting to Product silently loses those platforms.
    """
    for node in _iter_ld_objects(html):
        types = str(node.get("@type", "")).lower()
        if "product" not in types and "drug" not in types:
            continue
        name = _unescape(node.get("name") or "").strip()
        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = next((o for o in offers if isinstance(o, dict)), {})
        if not isinstance(offers, dict):
            offers = {}
        sale = _num(offers.get("price") or offers.get("lowPrice"))
        mrp = _num(offers.get("highPrice")) or sale
        avail = str(offers.get("availability") or "").lower().replace("_", "")
        in_stock = not any(flag in avail for flag in _OOS_AVAIL) if avail else True
        if name and sale > 0:
            brand = node.get("manufacturer") or node.get("brand") or ""
            if isinstance(brand, dict):
                brand = brand.get("name") or ""
            return {
                "name": name,
                "sale_price": sale,
                "mrp": mrp if mrp >= sale else sale,
                "in_stock": in_stock,
                "manufacturer": _unescape(str(brand)).strip(),
                "image_url": _unescape(_ld_image(node.get("image"))).strip(),
            }
    return None


def _extract_from_state(html: str) -> Optional[Dict]:
    """Last resort: probe an inlined redux/preloaded state blob.

    1mg emits no JSON-LD and no __NEXT_DATA__ — its PDP ships the product in a
    `window.__PRELOADED_STATE__`-style blob. Rather than parse that whole tree,
    scan for the price/name field names it uses. Deliberately narrow: requires
    BOTH a plausible name and a positive price, else returns None.
    """
    name = ""
    for key in ("label_name", "display_name", "name"):
        for m in re.finditer(rf'"{key}"\s*:\s*"([^"]{{4,120}})"', html):
            cand = _unescape(m.group(1)).strip()
            # Skip meta/app junk ("apple-itunes-app") and pure URLs.
            if cand and not cand.startswith("http") and "-app" not in cand:
                name = cand
                break
        if name:
            break
    sale = 0.0
    for key in ("discounted_price", "selling_price", "price"):
        m = re.search(rf'"{key}"\s*:\s*"?([\d.]+)', html)
        if m and _num(m.group(1)) > 0:
            sale = _num(m.group(1))
            break
    mrp_m = re.search(r'"mrp"\s*:\s*"?([\d.]+)', html)
    mrp = _num(mrp_m.group(1)) if mrp_m else 0.0
    if not sale and mrp > 0:
        sale = mrp  # no-discount product: MRP IS the sale price
    if not (name and sale > 0):
        return None
    return {
        "name": name,
        "sale_price": sale,
        "mrp": mrp if mrp >= sale else sale,
        "in_stock": _page_in_stock(html),
        "manufacturer": "",
    }


def _extract_from_next(html: str) -> Optional[Dict]:
    """Fallback: probe __NEXT_DATA__ for the common price/name field names."""
    m = _NEXT_RE.search(html)
    if not m:
        return None
    blob = m.group(1)
    name = ""
    for key in ("productName", "displayName", "skuName", "title", "name"):
        nm = re.search(rf'"{key}"\s*:\s*"([^"]{{3,120}})"', blob)
        if nm:
            name = nm.group(1).strip()
            break
    sale = 0.0
    for key in ("salePriceDecimal", "salePrice", "sellingPrice", "finalPrice",
                "discountedPrice", "price"):
        pm = re.search(rf'"{key}"\s*:\s*"?([\d.]+)', blob)
        if pm:
            sale = _num(pm.group(1))
            if sale > 0:
                break
    mrp = 0.0
    for key in ("mrpDecimal", "mrp", "originalPrice", "listPrice"):
        mm = re.search(rf'"{key}"\s*:\s*"?([\d.]+)', blob)
        if mm:
            mrp = _num(mm.group(1))
            if mrp > 0:
                break
    if not (name and sale > 0):
        return None
    stock_m = re.search(r'"(?:isAvailable|inStock|available)"\s*:\s*(true|false)', blob)
    in_stock = coerce_stock(stock_m.group(1) == "true") if stock_m else True
    mfr_m = re.search(r'"(?:manufacturerName|manufacturer|brandName)"\s*:\s*"([^"]{2,80})"', blob)
    return {
        "name": _unescape(name),
        "sale_price": sale,
        "mrp": mrp if mrp >= sale else sale,
        "in_stock": in_stock,
        "manufacturer": _unescape(mfr_m.group(1)).strip() if mfr_m else "",
    }


class PdpFetcher(BaseScraper):
    """Fetches a KNOWN product URL and parses it into a standardized price row.

    Platform-agnostic on purpose: the platform label is supplied by the caller
    (it owns the URL, so it knows which platform it came from), and extraction
    relies on markup every storefront emits rather than per-site selectors.
    """

    def fetch(self, platform: str, url: str) -> Optional[Dict]:
        """Return a standardized result dict for `url`, or None if unusable."""
        if not url or not url.startswith("http"):
            return None
        html = self.get(url)
        if not html:
            logger.debug(f"[pdp:{platform}] no HTML for {url}")
            return None

        parsed = (_extract_from_ld(html)
                  or _extract_from_next(html)
                  or _extract_from_state(html))
        if not parsed:
            logger.debug(f"[pdp:{platform}] no parseable product data in {url}")
            return None

        # Prefer the JSON-LD `image` (the product photo the storefront declares),
        # else og:image, which every one of these PDPs emits for social sharing.
        # Resolve relative paths against this URL's own origin, not a hardcoded
        # base — this fetcher is platform-agnostic. "" means no image for this
        # platform; the UI falls back to the platform logo.
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        image_url = clean_image_url(parsed.get("image_url"), origin) \
            or clean_image_url(_og_image(html), origin)

        return {
            "platform": platform,
            "name": parsed["name"],
            "url": url,
            "mrp": parsed["mrp"],
            "sale_price": parsed["sale_price"],
            "discount_percent": (
                round((parsed["mrp"] - parsed["sale_price"]) / parsed["mrp"] * 100, 2)
                if parsed["mrp"] > parsed["sale_price"] > 0 else 0.0
            ),
            "pack_size": "",
            "manufacturer": parsed.get("manufacturer", ""),
            "in_stock": bool(parsed["in_stock"]),
            "image_url": image_url,
            "source": "pdp_direct",
        }
