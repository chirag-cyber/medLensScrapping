"""
detail_scraper.py — Comprehensive clinical detail scraper.

Extracts complete medicine information from 1mg product pages:
  - Composition (salt + strength)
  - Description / Product Introduction
  - Uses & Benefits
  - Side Effects
  - How to Use
  - How it Works (Mechanism of Action)
  - Safety Advice (Alcohol, Pregnancy, Breastfeeding, Driving, Kidney, Liver)
  - Quick Tips
  - Drug Interactions (top 10)
  - Substitutes / Alternate Brands
  - FAQs
  - Manufacturer
  - Storage conditions
  - Fact Box (habit forming, therapeutic class)
  - Missed dose advice

Strategy:
  1. Primary: 1mg product page (requests + HTML/JSON-LD parsing — no Playwright needed)
  2. Fallback: Netmeds prescription page (requests + HTML parsing)
"""

import asyncio
import logging
import json
import re
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ClinicalDetailScraper(BaseScraper):
    """
    Comprehensive clinical data extractor.
    Uses requests (fast, reliable) — no Playwright required.
    """

    def __init__(self, delay: float = 0.5):
        super().__init__(delay=delay)

    # ================================================================
    #  PUBLIC API
    # ================================================================

    def fetch_clinical_data(self, onemg_url: str = None, netmeds_url: str = None) -> Dict:
        """
        Fetch comprehensive clinical data. Tries 1mg first, then Netmeds.
        Returns a standardized clinical_info dict.
        """
        clinical = self._empty_clinical()

        # Strategy 1: 1mg (richest source)
        if onemg_url:
            try:
                clinical = self._fetch_1mg_clinical(onemg_url)
                if clinical.get("description") and clinical.get("composition"):
                    logger.info("[1mg] Successfully extracted clinical data")
                    return clinical
            except Exception as e:
                logger.warning(f"[1mg] Clinical fetch failed: {e}")

        # Strategy 2: Netmeds fallback
        if netmeds_url:
            try:
                netmeds_data = self._fetch_netmeds_clinical(netmeds_url)
                clinical = self._merge_clinical(clinical, netmeds_data)
                if clinical.get("description") or clinical.get("uses"):
                    logger.info("[Netmeds] Used as clinical data fallback")
            except Exception as e:
                logger.warning(f"[Netmeds] Clinical fetch failed: {e}")

        return clinical

    async def fetch_clinical_data_async(self, onemg_url: str = None, netmeds_url: str = None) -> Dict:
        """Async wrapper."""
        return await asyncio.to_thread(self.fetch_clinical_data, onemg_url, netmeds_url)

    # ================================================================
    #  1MG CLINICAL EXTRACTION
    # ================================================================

    def _fetch_1mg_clinical(self, url: str) -> Dict:
        """Extract comprehensive clinical data from a 1mg drug page."""
        html = self.get(url)
        if not html:
            return self._empty_clinical()

        soup = BeautifulSoup(html, 'lxml')
        clinical = self._empty_clinical()

        # ---- 1. JSON-LD structured data (richest source) ----
        self._parse_jsonld(soup, clinical)

        # ---- 2. Composition/Salt ----
        salt_el = soup.select_one('a[href*="/generics/"]')
        if salt_el:
            clinical["composition"] = salt_el.get_text(strip=True)

        # ---- 3. HTML clinical sections (DrugOverview pattern) ----
        self._parse_drug_overview_sections(soup, clinical)

        # ---- 4. Safety Advice (structured cards) ----
        self._parse_safety_advice(soup, clinical)

        # ---- 5. Quick Tips ----
        self._parse_section_list(soup, "quick tips", "quick_tips", clinical)

        # ---- 6. Drug Interactions (top 10) ----
        self._parse_drug_interactions(soup, clinical)

        # ---- 7. Substitutes / Alternate Brands ----
        self._parse_substitutes(soup, clinical)

        # ---- 8. Fact Box ----
        self._parse_fact_box(soup, clinical)

        # ---- 9. FAQs (from JSON-LD FAQPage) ----
        self._parse_faqs_jsonld(soup, clinical)

        # ---- 10. Storage ----
        storage_el = soup.find(string=re.compile(r"store\s", re.I))
        if storage_el:
            text = storage_el.strip()
            if len(text) < 100:
                clinical["storage"] = text

        # ---- 11. Manufacturer (from JSON-LD or page) ----
        if not clinical.get("manufacturer"):
            marketer_el = soup.select_one('a[href*="/marketer/"]')
            if marketer_el:
                clinical["manufacturer"] = marketer_el.get_text(strip=True)

        return clinical

    # ================================================================
    #  JSON-LD PARSING
    # ================================================================

    def _parse_jsonld(self, soup: BeautifulSoup, clinical: Dict):
        """Parse JSON-LD Drug schema for rich structured data."""
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data.get('@type') != 'Drug':
                    continue

                # Description
                if data.get('description') and not clinical.get('description'):
                    clinical['description'] = data['description']

                # Manufacturer
                marketer = data.get('marketer', {})
                if isinstance(marketer, dict):
                    clinical['manufacturer'] = marketer.get('legalName', '')

                # Active ingredient
                if data.get('activeIngredient') and not clinical.get('composition'):
                    strength = ''
                    avail = data.get('availableStrength', [])
                    if avail and isinstance(avail, list):
                        s = avail[0]
                        strength = f" ({s.get('strengthValue', '')}{s.get('strengthUnit', '')})"
                    clinical['composition'] = data['activeIngredient'] + strength

                # Mechanism of action
                moa = data.get('mechanismOfAction', [])
                if moa:
                    text = moa[0] if isinstance(moa, list) else str(moa)
                    clinical['how_it_works'] = text.strip()

                # Safety warnings from JSON-LD
                safety = clinical.get('safety_advice', {})
                if data.get('alcoholWarning'):
                    safety['alcohol'] = {"status": "UNSAFE", "text": data['alcoholWarning']}
                if data.get('pregnancyWarning'):
                    safety['pregnancy'] = {"status": "CAUTION", "text": data['pregnancyWarning']}
                if data.get('breastfeedingWarning'):
                    safety['breastfeeding'] = {"status": "SAFE", "text": data['breastfeedingWarning']}
                clinical['safety_advice'] = safety

                # Prescription status
                clinical['prescription_status'] = data.get('prescriptionStatus', '')

                # Dosage form & route
                clinical['dosage_form'] = data.get('dosageForm', '')
                clinical['administration_route'] = data.get('administrationRoute', '')

            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"JSON-LD parse error: {e}")

    def _parse_faqs_jsonld(self, soup: BeautifulSoup, clinical: Dict):
        """Parse FAQ structured data from JSON-LD FAQPage."""
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if data.get('@type') != 'FAQPage':
                    continue

                faqs = []
                for entity in data.get('mainEntity', []):
                    q = entity.get('name', '').strip()
                    a = entity.get('acceptedAnswer', {}).get('text', '').strip()
                    if q and a:
                        faqs.append({"question": q, "answer": a})

                if faqs:
                    clinical['faqs'] = faqs

            except (json.JSONDecodeError, Exception):
                pass

    # ================================================================
    #  HTML SECTION PARSING (DrugOverview pattern)
    # ================================================================

    SECTION_MAP = {
        'product introduction': 'description',
        'uses of': 'uses',
        'benefits of': 'benefits',
        'side effects of': 'side_effects',
        'how to use': 'how_to_use',
        'works': 'how_it_works',
        'what if you forget': 'missed_dose',
    }

    def _parse_drug_overview_sections(self, soup: BeautifulSoup, clinical: Dict):
        """Parse DrugOverview sections from HTML."""
        headings = soup.find_all('h2', class_=lambda c: c and any(
            'DrugOverview__title' in cls for cls in (c if isinstance(c, list) else [c])
        ))

        for heading in headings:
            heading_text = heading.get_text(strip=True).lower()

            field_name = None
            for keyword, field in self.SECTION_MAP.items():
                if keyword in heading_text:
                    field_name = field
                    break
            if not field_name:
                continue

            # Skip if we already have good data from JSON-LD
            if field_name in clinical and clinical[field_name]:
                if field_name in ('description', 'how_it_works'):
                    continue

            # Get content from next sibling div with DrugOverview__content class
            content_div = heading.find_next_sibling('div', class_=lambda c: c and any(
                'DrugOverview__content' in cls for cls in (c if isinstance(c, list) else [c])
            ))
            if not content_div:
                continue

            if field_name in ('uses', 'benefits'):
                # Extract as list
                items = content_div.find_all('li')
                if items:
                    values = []
                    for li in items:
                        t = li.get_text(strip=True)
                        # Clean up "show more/show less" artifacts
                        t = re.sub(r'show\s*more\s*show\s*less', '', t, flags=re.I).strip()
                        if t and len(t) > 2:
                            values.append(t)
                    if field_name == 'uses':
                        clinical['uses'] = values
                    else:
                        # Merge benefits into uses, skip if it's just expanded text
                        existing = clinical.get('uses', [])
                        # Only add genuinely new items (not duplicates of existing)
                        for v in values:
                            if v not in existing:
                                existing.append(v)
                        clinical['uses'] = existing
                else:
                    text = content_div.get_text(strip=True)
                    text = re.sub(r'show\s*more\s*show\s*less', '', text, flags=re.I).strip()
                    if text and len(text) > 5:
                        # For benefits, this is often the expanded paragraph — 
                        # don't add as a use, store separately if no uses yet
                        if field_name == 'uses':
                            clinical['uses'] = clinical.get('uses', []) + [text]

            elif field_name == 'side_effects':
                # Combine intro text + list items
                parts = []
                # Check for intro paragraph
                for child in content_div.children:
                    if hasattr(child, 'name'):
                        if child.name == 'p':
                            t = child.get_text(strip=True)
                            if t:
                                parts.append(t)
                        elif child.name in ('ul', 'ol'):
                            for li in child.find_all('li'):
                                t = li.get_text(strip=True)
                                if t:
                                    parts.append(t)

                # Also look for side effect sub-sections
                se_heading = heading.find_next('h3')
                if se_heading:
                    for sib in se_heading.find_next_siblings():
                        if sib.name == 'h2':
                            break
                        if sib.name == 'div':
                            lis = sib.find_all('li')
                            for li in lis:
                                t = li.get_text(strip=True)
                                if t and t not in parts:
                                    parts.append(t)

                if not parts:
                    text = content_div.get_text(strip=True)
                    if text:
                        parts = [text]

                clinical['side_effects'] = parts

            else:
                # General text extraction
                text = content_div.get_text(separator='\n', strip=True)
                if text:
                    clinical[field_name] = text

    # ================================================================
    #  SAFETY ADVICE PARSING
    # ================================================================

    def _parse_safety_advice(self, soup: BeautifulSoup, clinical: Dict):
        """Parse structured safety advice cards."""
        safety = clinical.get('safety_advice', {})

        # Find the safety advice heading
        sa_heading = None
        for h2 in soup.find_all('h2'):
            if 'safety advice' in h2.get_text(strip=True).lower():
                sa_heading = h2
                break

        if not sa_heading:
            return

        # Walk siblings to collect safety cards
        for sib in sa_heading.find_next_siblings():
            if sib.name == 'h2':
                break

            # Each safety item typically has a label and description
            cards = sib.find_all('div', recursive=False) if sib.name == 'div' else [sib]
            for card in cards:
                text = card.get_text(separator=' | ', strip=True)
                if not text or len(text) < 10:
                    continue

                # Detect category
                text_lower = text.lower()
                category = None
                status = "UNKNOWN"

                for cat in ['alcohol', 'pregnancy', 'breast feeding', 'breastfeeding',
                            'driving', 'kidney', 'liver']:
                    if cat in text_lower:
                        category = cat.replace('breast feeding', 'breastfeeding').replace(' ', '_')
                        break

                if not category:
                    continue

                # Already have from JSON-LD? Skip
                if category in safety and safety[category].get('text'):
                    continue

                # Detect status
                if 'unsafe' in text_lower:
                    status = "UNSAFE"
                elif 'safe' in text_lower and 'unsafe' not in text_lower:
                    status = "SAFE"
                elif 'caution' in text_lower:
                    status = "CAUTION"

                # Extract just the advice text (after the label)
                advice_text = text
                for label in ['Alcohol', 'Pregnancy', 'Breast Feeding', 'Breastfeeding',
                              'Driving', 'Kidney', 'Liver']:
                    advice_text = advice_text.replace(label, '', 1).strip(' |')

                safety[category] = {"status": status, "text": advice_text.strip()}

        clinical['safety_advice'] = safety

    # ================================================================
    #  QUICK TIPS, DRUG INTERACTIONS, SUBSTITUTES, FACT BOX
    # ================================================================

    def _find_section_content(self, soup: BeautifulSoup, heading_keyword: str):
        """Find h2 heading and return its content siblings.
        
        1mg wraps headings inside a div.DrugPane__title___* container.
        Content lives in the parent's next siblings, not inside the heading's parent.
        """
        heading = None
        for h2 in soup.find_all('h2'):
            if heading_keyword in h2.get_text(strip=True).lower():
                heading = h2
                break
        if not heading:
            return None, []

        parent = heading.parent
        if not parent:
            return heading, []

        # Collect siblings of the parent container until the next section
        content_siblings = []
        for sib in parent.find_next_siblings():
            # Stop if we hit another section heading
            if sib.find('h2'):
                break
            content_siblings.append(sib)
        return heading, content_siblings

    def _parse_section_list(self, soup: BeautifulSoup, heading_keyword: str,
                            field_name: str, clinical: Dict):
        """Parse a section that contains a list of items (quick tips, etc.)."""
        _, siblings = self._find_section_content(soup, heading_keyword)
        if not siblings:
            return

        items = []
        for sib in siblings:
            for li in sib.find_all('li'):
                text = li.get_text(strip=True)
                if text:
                    items.append(text)
        if items:
            clinical[field_name] = items

    def _parse_drug_interactions(self, soup: BeautifulSoup, clinical: Dict):
        """Parse drug interaction cards (top 10)."""
        _, siblings = self._find_section_content(soup, 'interaction with drug')
        if not siblings:
            return

        interactions = []
        seen_drugs = set()

        for sib in siblings:
            # Each interaction item is typically a nested div
            items = sib.find_all('div', recursive=True)
            for item in items:
                text = item.get_text(separator=' ', strip=True)
                if len(text) < 15 or len(text) > 500:
                    continue

                # Look for severity indicators
                severity = "UNKNOWN"
                text_lower = text.lower()
                if 'severe' in text_lower:
                    severity = "SEVERE"
                elif 'moderate' in text_lower:
                    severity = "MODERATE"
                elif 'mild' in text_lower:
                    severity = "MILD"
                else:
                    continue

                # Extract drug name (usually the first bold/strong text)
                drug_name_el = item.find(['strong', 'b', 'h3', 'h4'])
                drug_name = drug_name_el.get_text(strip=True) if drug_name_el else ""

                if not drug_name or drug_name in seen_drugs:
                    continue
                seen_drugs.add(drug_name)

                desc = text.replace(drug_name, '', 1).strip()
                desc = re.sub(r'^[\s|:.-]+', '', desc)

                interactions.append({
                    "drug": drug_name,
                    "severity": severity,
                    "description": desc[:200]
                })

                if len(interactions) >= 10:
                    break
            if len(interactions) >= 10:
                break

        clinical['drug_interactions'] = interactions

    def _parse_substitutes(self, soup: BeautifulSoup, clinical: Dict):
        """Parse alternate brand substitutes."""
        _, siblings = self._find_section_content(soup, 'alternate brand')
        if not siblings:
            return

        substitutes = []
        seen = set()

        for sib in siblings:
            # Look for links to drug pages
            links = sib.find_all('a', href=lambda h: h and '/drugs/' in h)
            for link in links:
                name = link.get_text(strip=True)
                if not name or name in seen or len(name) < 3:
                    continue
                seen.add(name)

                href = link.get('href', '')
                url = f"https://www.1mg.com{href}" if href.startswith('/') else href

                # Find price in the closest parent row
                row = link.parent
                # Walk up to find a container with price info
                for _ in range(5):
                    if row and row.parent:
                        row_text = row.get_text()
                        if '₹' in row_text or '\u20b9' in row_text:
                            break
                        row = row.parent
                    else:
                        break

                price = 0.0
                if row:
                    price_match = re.search(r'[\u20b9₹]\s*([\d,.]+)', row.get_text())
                    if price_match:
                        try:
                            price = float(price_match.group(1).replace(',', ''))
                        except ValueError:
                            pass

                # Manufacturer
                manufacturer = ""
                if row:
                    mfg_el = row.find(string=re.compile(r'(?:by|mfg|manufacturer)', re.I))
                    if mfg_el:
                        manufacturer = mfg_el.parent.get_text(strip=True) if mfg_el.parent else ""
                        manufacturer = re.sub(r'^(?:by|mfg|manufacturer)\s*:?\s*', '', manufacturer, flags=re.I)

                substitutes.append({
                    "name": name,
                    "url": url,
                    "price": price,
                    "manufacturer": manufacturer
                })

                if len(substitutes) >= 10:
                    break
            if len(substitutes) >= 10:
                break

        clinical['substitutes'] = substitutes

    def _parse_fact_box(self, soup: BeautifulSoup, clinical: Dict):
        """Parse the fact box section."""
        _, siblings = self._find_section_content(soup, 'fact box')
        if not siblings:
            return

        fact_box = {}
        # Combine text from all siblings
        full_text = '\n'.join(sib.get_text(separator='\n', strip=True) for sib in siblings)

        # Parse key-value patterns
        if 'habit forming' in full_text.lower():
            match = re.search(r'habit\s*forming\s*[:\-]?\s*(yes|no)', full_text, re.I)
            if match:
                fact_box['habit_forming'] = match.group(1).capitalize()

        if 'therapeutic class' in full_text.lower():
            match = re.search(r'therapeutic\s*class\s*[:\-]?\s*(.+?)(?:\n|$)', full_text, re.I)
            if match:
                fact_box['therapeutic_class'] = match.group(1).strip()

        if 'action class' in full_text.lower():
            match = re.search(r'action\s*class\s*[:\-]?\s*(.+?)(?:\n|$)', full_text, re.I)
            if match:
                fact_box['action_class'] = match.group(1).strip()

        if fact_box:
            clinical['fact_box'] = fact_box

    # ================================================================
    #  NETMEDS FALLBACK
    # ================================================================

    def _fetch_netmeds_clinical(self, url: str) -> Dict:
        """Extract clinical data from a Netmeds prescription page (fallback)."""
        # Convert product URL to prescriptions URL if needed
        if '/product/' in url:
            url = re.sub(r'/product/([^/]+?)(?:-[a-z0-9]{6,}-\d+)?$',
                         r'/prescriptions/\1', url)
            if 'netmeds.com' not in url:
                url = f"https://www.netmeds.com{url}"

        html = self.get(url)
        if not html:
            return self._empty_clinical()

        soup = BeautifulSoup(html, 'lxml')
        clinical = self._empty_clinical()

        # Composition
        salt_tag = soup.select_one('.drug-manu')
        if salt_tag:
            clinical['composition'] = salt_tag.get_text(strip=True)

        # Sections via h2 headings
        section_map = {
            'introduction about': 'description',
            'uses of': 'uses',
            'side effects of': 'side_effects',
            'how to use': 'how_to_use',
            'works': 'how_it_works',
            'safety advice': 'safety_advice_text',
        }

        for h2 in soup.find_all('h2'):
            h2_text = h2.get_text(strip=True).lower()
            for keyword, field in section_map.items():
                if keyword in h2_text:
                    content = self._extract_sibling_content(h2)
                    if content:
                        if field == 'uses':
                            clinical['uses'] = content if isinstance(content, list) else [content]
                        elif field == 'side_effects':
                            clinical['side_effects'] = content if isinstance(content, list) else [content]
                        else:
                            clinical[field] = content if isinstance(content, str) else '\n'.join(content)
                    break

        # FAQs
        faqs = []
        faq_questions = soup.find_all('h3', class_='nms-faqpage-question')
        for q_tag in faq_questions:
            q = re.sub(r'^Q:\s*', '', q_tag.get_text(strip=True)).strip()
            a_tag = q_tag.find_next_sibling('div')
            a = re.sub(r'^A:\s*', '', a_tag.get_text(strip=True)).strip() if a_tag else ''
            if q and a:
                faqs.append({"question": q, "answer": a})
        if faqs:
            clinical['faqs'] = faqs

        return clinical

    def _extract_sibling_content(self, heading) -> str | list:
        """Extract content from siblings until the next h2."""
        parts = []
        for sib in heading.find_next_siblings():
            if sib.name == 'h2':
                break
            if sib.name == 'ul':
                items = [li.get_text(strip=True) for li in sib.find_all('li') if li.get_text(strip=True)]
                if items:
                    return items
            elif sib.name in ('p', 'div'):
                text = sib.get_text(strip=True)
                if text and len(text) > 5:
                    parts.append(text)
        return '\n'.join(parts) if parts else None

    # ================================================================
    #  UTILITIES
    # ================================================================

    @staticmethod
    def _empty_clinical() -> Dict:
        """Return an empty clinical data template."""
        return {
            "composition": "",
            "description": "",
            "uses": [],
            "side_effects": [],
            "how_to_use": "",
            "how_it_works": "",
            "safety_advice": {},
            "quick_tips": [],
            "drug_interactions": [],
            "substitutes": [],
            "faqs": [],
            "manufacturer": "",
            "storage": "",
            "fact_box": {},
            "missed_dose": "",
            "prescription_status": "",
            "dosage_form": "",
            "administration_route": "",
        }

    @staticmethod
    def _merge_clinical(primary: Dict, secondary: Dict) -> Dict:
        """Merge clinical data, preferring non-empty primary fields."""
        merged = dict(primary)
        for key, value in secondary.items():
            if not merged.get(key):
                merged[key] = value
            elif isinstance(value, list) and not merged[key]:
                merged[key] = value
            elif isinstance(value, dict) and not merged[key]:
                merged[key] = value
        return merged


# ================================================================
#  STANDALONE TEST
# ================================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    test_url = "https://www.1mg.com/drugs/dolo-650-tablet-74467"
    if len(sys.argv) > 1:
        test_url = sys.argv[1]

    scraper = ClinicalDetailScraper(delay=0.5)
    print(f"Fetching clinical data from: {test_url}")
    data = scraper.fetch_clinical_data(onemg_url=test_url)

    # Pretty print summary
    print(f"\n{'='*60}")
    print("CLINICAL DATA SUMMARY")
    print(f"{'='*60}")
    print(f"  Composition: {data.get('composition', 'N/A')}")
    print(f"  Manufacturer: {data.get('manufacturer', 'N/A')}")
    print(f"  Rx Status: {data.get('prescription_status', 'N/A')}")
    print(f"  Description: {(data.get('description') or 'N/A')[:150]}...")
    print(f"  Uses: {data.get('uses', [])}")
    print(f"  Side Effects: {data.get('side_effects', [])[:5]}")
    print(f"  How to Use: {(data.get('how_to_use') or 'N/A')[:100]}...")
    print(f"  How it Works: {(data.get('how_it_works') or 'N/A')[:100]}...")
    print(f"  Safety Advice: {list(data.get('safety_advice', {}).keys())}")
    print(f"  Quick Tips: {len(data.get('quick_tips', []))} tips")
    print(f"  Drug Interactions: {len(data.get('drug_interactions', []))} interactions")
    print(f"  Substitutes: {len(data.get('substitutes', []))} alternatives")
    print(f"  FAQs: {len(data.get('faqs', []))} questions")
    print(f"  Storage: {data.get('storage', 'N/A')}")
    print(f"  Fact Box: {data.get('fact_box', {})}")
    print(f"  Missed Dose: {(data.get('missed_dose') or 'N/A')[:100]}...")

    # Full JSON dump
    print(f"\n{'='*60}")
    print("FULL JSON OUTPUT")
    print(f"{'='*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    scraper.close()
