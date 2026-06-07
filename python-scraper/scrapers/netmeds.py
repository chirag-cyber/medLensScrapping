"""
netmeds.py — Direct Netmeds.com scraper using requests + BeautifulSoup.

Scrapes clinical data from Netmeds prescription pages:
    - Introduction / Description
    - Uses
    - Side Effects
    - How to Use
    - How It Works
    - Safety Advice
    - FAQs

Note: Only /prescriptions/ URLs have clinical data.
      /product/ URLs are just shopping pages.
"""

import re
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class NetmedsScraper(BaseScraper):
    """
    Scrape clinical data from Netmeds prescription pages.

    Usage:
        scraper = NetmedsScraper(delay=1.5)
        data = scraper.scrape_url("https://www.netmeds.com/prescriptions/dolo-650mg-tablet-15-s")
        data = scraper.scrape_by_name("dolo 650mg tablet")
        scraper.close()
    """

    BASE_URL = "https://www.netmeds.com"

    # Heading text → field mapping
    SECTION_MAP = {
        'introduction about': 'description',
        'uses of': 'uses',
        'side effects of': 'side_effects',
        'how to use': 'how_to_use',
        'works': 'how_it_works',
        'safety advice': 'safety_advice',
    }

    def scrape_url(self, url: str) -> dict | None:
        """Scrape clinical data from a Netmeds URL."""
        # Ensure we're on a prescriptions page, not a product page
        if '/product/' in url:
            url = self._product_to_prescriptions_url(url)

        html = self.get(url)
        if not html:
            return None
        return self._parse_html(html)

    def scrape_by_name(self, name: str) -> dict | None:
        """Search for a medicine by name on Netmeds."""
        slug = self._name_to_slug(name)
        url = f"{self.BASE_URL}/prescriptions/{slug}"
        return self.scrape_url(url)

    def _parse_html(self, html: str) -> dict | None:
        """Parse Netmeds prescription page HTML."""
        soup = BeautifulSoup(html, 'lxml')

        # Verify it's not an error page
        title = soup.find('title')
        if title:
            title_text = title.get_text(strip=True).lower()
            if 'not found' in title_text or '404' in title_text:
                return None

        result = {}

        # Extract medicine name from title
        if title:
            med_name = self._extract_medicine_name(title.get_text(strip=True))
            if med_name:
                result['medicine_name'] = med_name

        # ── Extract clinical sections ──
        # Netmeds uses plain <h2> tags with descriptive text
        for h2 in soup.find_all('h2'):
            heading_text = h2.get_text(strip=True).lower()

            field_name = None
            for keyword, field in self.SECTION_MAP.items():
                if keyword in heading_text:
                    field_name = field
                    break

            if not field_name:
                continue

            # Get content from next siblings until next h2
            content = self._extract_section_content(h2, field_name)
            if content:
                result[field_name] = content

        # ── Extract FAQs ──
        faqs = self._extract_faqs(soup)
        if faqs:
            result['faq'] = faqs

        return result if result else None

    def _extract_section_content(self, heading, field_name: str) -> str | None:
        """Extract text content between this heading and the next h2."""
        parts = []

        for sibling in heading.find_next_siblings():
            # Stop at the next h2 heading
            if sibling.name == 'h2':
                break

            if field_name == 'uses':
                # Uses are typically in <ul> lists
                if sibling.name == 'ul':
                    items = sibling.find_all('li')
                    parts.extend(li.get_text(strip=True) for li in items if li.get_text(strip=True))
                    break

            elif field_name == 'safety_advice':
                # Safety advice is in <ul> with structured items
                if sibling.name == 'ul':
                    items = sibling.find_all('li')
                    parts.extend(li.get_text(separator=' ', strip=True) for li in items if li.get_text(strip=True))
                    break

            elif field_name == 'side_effects':
                text = sibling.get_text(strip=True)
                if text and len(text) > 5:
                    parts.append(text)
                # Also get nested lists
                items = sibling.find_all('li')
                if items:
                    parts.extend(li.get_text(strip=True) for li in items if li.get_text(strip=True))

            else:
                # General: get paragraphs, sub-headings, and lists
                if sibling.name == 'p':
                    text = sibling.get_text(strip=True)
                    if text:
                        parts.append(text)
                elif sibling.name == 'h3':
                    text = sibling.get_text(strip=True)
                    if text:
                        parts.append(text)
                elif sibling.name == 'ul':
                    items = sibling.find_all('li')
                    parts.extend(li.get_text(strip=True) for li in items if li.get_text(strip=True))
                elif sibling.name == 'div':
                    text = sibling.get_text(separator='\n', strip=True)
                    if text and len(text) > 10:
                        parts.append(text)

        return '\n'.join(parts) if parts else None

    def _extract_faqs(self, soup) -> list | None:
        """Extract FAQ Q&A pairs."""
        faqs = []

        # Netmeds FAQ structure: <h3 class="nms-faqpage-question">Q: ...</h3>
        # followed by <div>A: ...</div>
        faq_questions = soup.find_all('h3', class_='nms-faqpage-question')

        if not faq_questions:
            # Alternate: find FAQ section and look for Q/A patterns
            faq_heading = None
            for h2 in soup.find_all('h2'):
                if 'faq' in h2.get_text(strip=True).lower():
                    faq_heading = h2
                    break

            if faq_heading:
                faq_container = faq_heading.find_next_sibling('div', class_='nms-faqpage')
                if faq_container:
                    faq_questions = faq_container.find_all('h3')

        for q_tag in faq_questions:
            q_text = q_tag.get_text(strip=True)
            # Remove "Q: " prefix
            q_text = re.sub(r'^Q:\s*', '', q_text).strip()
            if not q_text:
                continue

            # Answer is the next sibling div
            a_tag = q_tag.find_next_sibling('div')
            a_text = ''
            if a_tag:
                a_text = a_tag.get_text(strip=True)
                a_text = re.sub(r'^A:\s*', '', a_text).strip()

            if q_text and a_text:
                faqs.append({'question': q_text, 'answer': a_text})

        return faqs if faqs else None

    def _extract_medicine_name(self, title: str) -> str:
        """Extract medicine name from page title."""
        # Netmeds titles: "DOLO 650MG TABLET 15's | Netmeds" or similar
        name = title.split('|')[0].strip()
        name = re.sub(r"'\s*s$", '', name).strip()  # Remove 's
        return name

    def _product_to_prescriptions_url(self, url: str) -> str:
        """Convert a /product/ URL to a /prescriptions/ URL."""
        # /product/dolo-650mg-tablet-15s-lui1wb-8231049  ->  /prescriptions/dolo-650mg-tablet-15-s
        match = re.search(r'/product/([^/]+?)(?:-[a-z0-9]{6,}-\d+)?$', url)
        if match:
            slug = match.group(1)
            return f"{self.BASE_URL}/prescriptions/{slug}"
        return url.replace('/product/', '/prescriptions/')

    @staticmethod
    def _name_to_slug(name: str) -> str:
        """Convert medicine name to Netmeds URL slug."""
        slug = name.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug.strip('-')
