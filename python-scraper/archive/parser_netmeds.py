"""
parser_netmeds.py — Extracts structured clinical data from Netmeds markdown (via Jina Reader).

Netmeds pages have a different structure than 1mg:
  - ## Introduction About {NAME}  (description paragraphs)
  - ## Uses Of {NAME}             (bullet list of uses + detailed explanations)
  - ## Side Effects Of {NAME}     (Common side effects bullets + when to consult)
  - ### Pregnancy / Breastfeeding / Driving / Alcohol / Kidney / Liver  (safety blocks)
  - ## Safety Advice              (bullet list)
  - ## Drug - Drug interaction    (numbered list)
  - ### Q: {question}  / A: {answer}  (FAQs)
"""

import re


def parse_netmeds_markdown(markdown: str) -> dict:
    """
    Parse Jina Reader markdown from a Netmeds product page into structured fields.
    Returns a dict with keys: description, uses, side_effects, how_to_use,
    how_it_works, safety_advice, faq
    """
    result = {}

    # Strip the Jina header
    content_match = re.search(r'Markdown Content:\s*\n', markdown)
    if content_match:
        markdown = markdown[content_match.end():]

    # Split into sections by ## headings
    sections = re.split(r'\n(?=## )', markdown)

    # --- 1. Description (Introduction About section) ---
    for section in sections:
        if re.match(r'## Introduction About', section, re.IGNORECASE):
            lines = section.split('\n', 1)
            if len(lines) > 1:
                desc = lines[1].strip()
                # Clean markdown links
                desc = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', desc)
                # Remove image references
                desc = re.sub(r'!\[.*?\]\(.*?\)', '', desc)
                desc = desc.strip()
                if desc and len(desc) > 30:
                    result['description'] = desc
            break

    # --- 2. Uses ---
    for section in sections:
        if re.match(r'## Uses Of', section, re.IGNORECASE):
            lines = section.split('\n', 1)
            if len(lines) > 1:
                uses_text = lines[1].strip()
                # Extract bullet points as the primary uses
                bullets = re.findall(r'\*\s+(.+?)(?:\n|$)', uses_text)
                if bullets:
                    # Take the top-level bullet points (usually the concise list)
                    uses_clean = '\n'.join(b.strip() for b in bullets[:10])
                    uses_clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', uses_clean)
                    if uses_clean:
                        result['uses'] = uses_clean
            break

    # --- 3. Side Effects ---
    for section in sections:
        if re.match(r'## Side Effects Of', section, re.IGNORECASE):
            lines = section.split('\n', 1)
            if len(lines) > 1:
                se_text = lines[1].strip()
                # Look for "Common side effects" block
                common_match = re.search(
                    r'(?:Common side effects.*?:\s*\n)((?:\*\s+.+\n?)+)',
                    se_text, re.IGNORECASE
                )
                if common_match:
                    bullets = re.findall(r'\*\s+(.+)', common_match.group(1))
                    if bullets:
                        result['side_effects'] = '\n'.join(b.strip() for b in bullets)
                else:
                    # Try to get any bullet list
                    bullets = re.findall(r'\*\s+(.+)', se_text)
                    if bullets:
                        se_items = [b.strip() for b in bullets[:8]]
                        result['side_effects'] = '\n'.join(se_items)
            break

    # --- 4. How to Use (Directions for use / Dosage sections) ---
    for section in sections:
        heading_lower = section.split('\n')[0].lower()
        if 'how to take' in heading_lower or 'direction' in heading_lower:
            lines = section.split('\n', 1)
            if len(lines) > 1:
                content = lines[1].strip()
                content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
                content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
                content = content.strip()
                if content and len(content) > 10:
                    result['how_to_use'] = content
            break

    # If no explicit "how to use" section, look for dosage subsections
    if 'how_to_use' not in result:
        for section in sections:
            # Look for subsections about dosage
            if '### Recommended Dosage' in section or '### How to Take' in section:
                # Extract relevant content
                dosage_match = re.search(
                    r'### (?:Recommended Dosage|How to Take).*?\n(.*?)(?=\n###|\n## |\Z)',
                    section, re.DOTALL
                )
                if dosage_match:
                    content = dosage_match.group(1).strip()
                    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
                    bullets = re.findall(r'\*\s+(.+)', content)
                    if bullets:
                        result['how_to_use'] = '\n'.join(b.strip() for b in bullets)
                break

    # --- 5. How it Works ---
    how_works_match = re.search(
        r'### \*\*How .+? Works.*?\*\*\s*\n(.*?)(?=\n###|\n## |\Z)',
        markdown, re.DOTALL
    )
    if how_works_match:
        content = how_works_match.group(1).strip()
        content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
        content = re.sub(r'!\[.*?\]\(.*?\)', '', content).strip()
        if content and len(content) > 10:
            result['how_it_works'] = content

    # --- 6. Safety Advice ---
    safety = _parse_netmeds_safety(markdown)
    if safety:
        result['safety_advice'] = safety

    # --- 7. FAQs ---
    faq_matches = re.findall(
        r'### Q:\s*(.+\?)\s*\nA:\s*(.*?)(?=\n### Q:|\n## |\Z)',
        markdown, re.DOTALL
    )
    faqs = []
    for question, answer in faq_matches:
        q = question.strip()
        a = answer.strip()
        a = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', a)
        if q and a and len(a) > 15:
            faqs.append({'question': q, 'answer': a})
    if faqs:
        result['faq'] = faqs

    return result


def _parse_netmeds_safety(markdown: str) -> str | None:
    """
    Parse safety advice from Netmeds markdown.
    Netmeds uses ### headings for each safety category with a status line.
    """
    categories = {
        'Pregnancy': r'### Pregnancy\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Breastfeeding': r'### Breastfeeding\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Breast feeding': r'### Breast feeding\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Driving': r'### Driving.*?\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Alcohol': r'### Alcohol\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Kidney': r'### Kidney\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
        'Liver': r'### Liver\s*\n(.*?)\n\n(.*?)(?=\n###|\n## |\Z)',
    }

    parts = []
    for cat, pattern in categories.items():
        match = re.search(pattern, markdown, re.DOTALL | re.IGNORECASE)
        if match:
            status = match.group(1).strip()
            detail = match.group(2).strip()
            # Clean markdown
            detail = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', detail)
            detail = re.sub(r'!\[.*?\]\(.*?\)', '', detail).strip()
            detail = re.sub(r'\n+', ' ', detail).strip()
            if detail:
                parts.append(f"{cat}: {status} - {detail}")
            elif status:
                parts.append(f"{cat}: {status}")

    return '\n'.join(parts) if parts else None


def extract_netmeds_medicine_name(title: str) -> str | None:
    """
    Extract medicine name from Netmeds page title.
    Format: "Dolo 650 Tablet 15's Price, Dosage, Substitute, Uses | Netmeds"
    """
    match = re.match(r"(.+?)\s*(?:Price|Dosage|'s\s+Price)", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None
