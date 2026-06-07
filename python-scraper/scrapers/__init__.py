"""Scrapers package — Independent pharmacy website scrapers."""
from scrapers.onemg import OneMGScraper
from scrapers.netmeds import NetmedsScraper
from scrapers.base import BaseScraper

__all__ = ['OneMGScraper', 'NetmedsScraper', 'BaseScraper']
