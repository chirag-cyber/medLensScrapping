"""
onemg.py — Direct 1mg.com scraper using requests + BeautifulSoup.

Scrapes clinical data from 1mg drug pages:
    - Product Introduction / Description
    - Uses
    - Side Effects
    - How to Use
    - How It Works
    - Safety Advice
    - FAQs

No external API dependencies. Pure Python.
"""

import re
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class OneMGScraper(BaseScraper):
    """
    Scrape clinical data from 1mg.com drug pages.

    Usage:
        scraper = OneMGScraper(delay=1.5)
        data = scraper.scrape_url("https://www.1mg.com/drugs/dolo-650-tablet-15-s-74467")
        data = scraper.scrape_by_name("dolo 650")
        scraper.close()
    """

    BASE_URL = "https://www.1mg.com"

    # ── Section heading keywords → output field names ──
    SECTION_MAP = {
        'product introduction': 'description',
        'uses of': 'uses',
        'side effects of': 'side_effects',
        'how to use': 'how_to_use',
        'works': 'how_it_works',      # "How {name} works"
        'safety advice': 'safety_advice',
    }

    def scrape_url(self, url: str) -> dict | None:
        """
        Scrape clinical data from a 1mg drug page URL.

        Returns dict with fields: description, uses, side_effects, how_to_use,
        how_it_works, safety_advice, faq, medicine_name
        Returns None if page fails to load or is not a drug page.
        """
        html = self.get(url)
        if not html:
            return None
        return self._parse_html(html)

    def scrape_by_name(self, name: str) -> dict | None:
        """
        Search for a medicine by name and scrape its clinical data.

        Constructs the URL from the medicine name slug.
        Returns dict or None.
        """
        slug = self._name_to_slug(name)
        url = f"{self.BASE_URL}/drugs/{slug}"

        result = self.scrape_url(url)
        if result:
            return result

        # Try alternate URL pattern (numeric ID URLs won't work with slug)
        # Some 1mg URLs are /drugs/{slug}-{id} format
        # If plain slug fails, we can't guess the ID, so return None
        return None

    def _parse_html(self, html: str) -> dict | None:
        """Parse 1mg drug page HTML and extract clinical sections."""
        soup = BeautifulSoup(html, 'lxml')

        # Verify this is a drug page (not a search or error page)
        title_tag = soup.find('title')
        if not title_tag:
            return None
        page_title = title_tag.get_text(strip=True)
        if 'page not found' in page_title.lower() or '404' in page_title:
            return None

        result = {}

        # Extract medicine name from the page
        # 1mg uses pattern: "Uses of {Medicine Name}" or page title
        med_name = self._extract_medicine_name(soup, page_title)
        if med_name:
            result['medicine_name'] = med_name

        # ── Extract clinical sections ──
        # Pattern: <h2 class="DrugOverview__title...">Heading</h2>
        #          <div class="DrugOverview__content...">Content</div>
        headings = soup.find_all('h2', class_=lambda c: c and any(
            'DrugOverview__title' in cls for cls in (c if isinstance(c, list) else [c])
        ))

        for heading in headings:
            heading_text = heading.get_text(strip=True).lower()

            # Match heading to our field map
            field_name = None
            for keyword, field in self.SECTION_MAP.items():
                if keyword in heading_text:
                    field_name = field
                    break

            if not field_name:
                continue

            # Get the content div (next sibling with DrugOverview__content class)
            content_div = heading.find_next_sibling('div', class_=lambda c: c and any(
                'DrugOverview__content' in cls for cls in (c if isinstance(c, list) else [c])
            ))

            if content_div:
                content = self._extract_text(content_div, field_name)
                if content:
                    result[field_name] = content

        # ── Extract Side Effects (special handling - combine intro + list) ──
        side_effects = self._extract_full_side_effects(soup)
        if side_effects:
            result['side_effects'] = side_effects

        # ── Extract FAQs ──
        faqs = self._extract_faqs(soup)
        if faqs:
            result['faq'] = faqs

        return result if result else None

    def _extract_medicine_name(self, soup, page_title: str) -> str:
        """Extract the medicine name from the page."""
        # From page title: "Buy Dolo 650 Tablet Online: Uses, Side Effects, Price & Dosage | 1mg"
        name = page_title.split('|')[0].strip()
        name = name.split(':')[0].strip()
        # Remove "Buy" prefix and "Online" suffix
        name = re.sub(r'^Buy\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Online$', '', name, flags=re.IGNORECASE)
        return name.strip()

    def _extract_text(self, div, field_name: str) -> str:
        """Extract clean text from a content div."""
        if field_name == 'uses':
            # Uses are typically in a list
            items = div.find_all('li')
            if items:
                return '\n'.join(li.get_text(strip=True) for li in items if li.get_text(strip=True))

        if field_name == 'safety_advice':
            return self._extract_safety_advice(div)

        # General text extraction
        # Remove nested script/style tags
        for tag in div.find_all(['script', 'style']):
            tag.decompose()

        # Get paragraphs
        paragraphs = div.find_all('p')
        if paragraphs:
            text = '\n'.join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
            if text:
                return text

        # Fallback: get all text
        text = div.get_text(separator='\n', strip=True)
        return text if text else None

    def _extract_safety_advice(self, div) -> str:
        """Extract safety advice which has a specific structure with labels + descriptions."""
        sections = []
        # Safety advice items are typically in a structured format
        # Each item has a label (Alcohol, Pregnancy, etc.) and advice text
        items = div.find_all('div', recursive=False)
        if not items:
            # Fallback: just get all text
            return div.get_text(separator='\n', strip=True)

        for item in items:
            text = item.get_text(separator=' ', strip=True)
            if text and len(text) > 5:
                sections.append(text)

        return '\n'.join(sections) if sections else div.get_text(separator='\n', strip=True)

    def _extract_full_side_effects(self, soup) -> str | None:
        """
        Extract FULL side effects — combines:
          1. Intro text from the DrugOverview__content div
          2. Actual list of side effects from the h3 'Common side effects' section
          3. Any additional side effects content
        """
        parts = []

        # Find the Side Effects h2 heading
        se_heading = None
        for h2 in soup.find_all('h2'):
            classes = h2.get('class', [])
            if any('DrugOverview__title' in c for c in classes):
                if 'side effect' in h2.get_text().lower():
                    se_heading = h2
                    break

        if not se_heading:
            return None

        # Collect ALL siblings until next DrugOverview h2 section
        for sib in se_heading.find_next_siblings():
            # Stop at next section heading (but not another side effects heading)
            if sib.name == 'h2':
                sib_classes = sib.get('class', [])
                if any('DrugOverview__title' in c for c in sib_classes):
                    if 'side effect' not in sib.get_text().lower():
                        break

            # Extract content from each sibling
            if sib.name == 'h3':
                # Sub-heading like "Common side effects of..."
                h3_text = sib.get_text(strip=True)
                if h3_text and 'common side' in h3_text.lower():
                    parts.append(h3_text)
            elif sib.name == 'div':
                # Could be intro text or the list container
                lis = sib.find_all('li')
                if lis:
                    for li in lis:
                        li_text = li.get_text(strip=True)
                        if li_text:
                            parts.append(f"• {li_text}")
                else:
                    text = sib.get_text(strip=True)
                    if text and len(text) > 10:
                        parts.append(text)

        return '\n'.join(parts) if parts else None

    def _extract_faqs(self, soup) -> list | None:
        """Extract FAQ questions and answers."""
        faqs = []

        # Find the FAQs section heading
        faq_heading = None
        for h2 in soup.find_all('h2'):
            if 'faq' in h2.get_text(strip=True).lower():
                faq_heading = h2
                break

        if not faq_heading:
            return None

        # FAQs are typically in a structured Q&A format after the heading
        # Look for the parent container and find Q/A pairs
        faq_container = faq_heading.find_parent('div')
        if not faq_container:
            return None

        # Look for question elements (h3 or specific class patterns)
        questions = faq_container.find_all(['h3', 'div'], class_=lambda c: c and any(
            'question' in cls.lower() or 'faq' in cls.lower()
            for cls in (c if isinstance(c, list) else [c])
        ))

        if not questions:
            # Try alternate: find all <h3> inside FAQ section
            questions = faq_container.find_all('h3')

        for q in questions:
            q_text = q.get_text(strip=True)
            if not q_text or len(q_text) < 10:
                continue

            # Find the answer (next sibling or nested div)
            answer_div = q.find_next_sibling(['div', 'p'])
            a_text = answer_div.get_text(strip=True) if answer_div else ''

            if q_text and a_text:
                faqs.append({'question': q_text, 'answer': a_text})

        return faqs if faqs else None

    @staticmethod
    def _name_to_slug(name: str) -> str:
        """Convert medicine name to 1mg URL slug."""
        slug = name.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug.strip('-')
