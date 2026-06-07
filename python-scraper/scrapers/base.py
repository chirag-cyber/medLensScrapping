"""
base.py — Resilient HTTP client for pharmacy scraping.

Features:
    - Session with connection pooling (reuses TCP connections)
    - Rotating User-Agent headers (10 real browser UAs)
    - Automatic retry with exponential backoff (429/500/503)
    - Configurable rate limiting
    - Request logging
"""

import time
import random
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Real browser User-Agent strings (rotated per request)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
]


class BaseScraper:
    """
    Base scraper with resilient HTTP session.

    Usage:
        scraper = BaseScraper(delay=1.5, max_retries=3)
        html = scraper.get("https://www.1mg.com/drugs/dolo-650-tablet-15-s-74467")
    """

    def __init__(self, delay: float = 1.5, max_retries: int = 3, timeout: int = 30):
        """
        Args:
            delay: Minimum seconds between requests (rate limiting)
            max_retries: Max retries on 429/500/503
            timeout: Request timeout in seconds
        """
        self.delay = delay
        self.timeout = timeout
        self._last_request_time = 0.0

        # Create session with connection pooling and retry
        self.session = requests.Session()

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=2,  # 2s, 4s, 8s backoff
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get_headers(self) -> dict:
        """Get request headers with a random User-Agent."""
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            sleep_time = self.delay - elapsed + random.uniform(0.1, 0.5)
            time.sleep(sleep_time)

    def get(self, url: str) -> str | None:
        """
        Fetch a URL and return the HTML content.

        Returns None on error (404, timeout, etc.)
        Handles rate limiting and retries automatically.
        """
        self._rate_limit()
        self._last_request_time = time.time()

        try:
            resp = self.session.get(
                url,
                headers=self._get_headers(),
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                logger.debug(f"[200] {url}")
                return resp.text
            elif resp.status_code == 429:
                logger.warning(f"[429] Rate limited: {url} — waiting 30s")
                time.sleep(30)
                # One more try
                resp = self.session.get(url, headers=self._get_headers(), timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                logger.error(f"[{resp.status_code}] Still failing after retry: {url}")
                return None
            elif resp.status_code == 404:
                logger.debug(f"[404] Not found: {url}")
                return None
            else:
                logger.warning(f"[{resp.status_code}] Unexpected: {url}")
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"[Timeout] {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[Connection Error] {url}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"[Request Error] {url}: {e}")
            return None

    def close(self):
        """Close the HTTP session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
