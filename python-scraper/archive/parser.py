"""
parser.py — Extracts structured clinical data from 1mg markdown (via Jina Reader).

1mg pages follow a highly consistent structure:
  - Intro paragraphs (product description)
  - ### In {Use Name}  (uses sections)
  - Side effects paragraph + bullet list
  - ## How to use {Name}
  - ## How {Name} works
  - ## Safety advice of {Name}  (Alcohol, Pregnancy, Breast feeding, Driving, Kidney, Liver)
  - ## What if you forget to take {Name}?
  - ## Alternate Brands
  - ## Quick tips
  - ## Fact Box
  - ## Interaction with drugs
  - ### FAQ questions
"""

import re


def parse_1mg_markdown(markdown: str) -> dict:
    """
    Parse Jina Reader markdown from a 1mg product page into structured fields.
    Returns a dict with keys: description, uses, side_effects, how_to_use,
    how_it_works, safety_advice, faq
    """
    result = {}

    # Strip the Jina header (everything before "Markdown Content:")
    content_match = re.search(r'Markdown Content:\s*\n', markdown)
    if content_match:
        markdown = markdown[content_match.end():]

    # Normalize horizontal rules to a consistent marker
    markdown = re.sub(r'\* \* \*', '---', markdown)

    # Split into sections by ## headings
    sections = re.split(r'\n(?=## )', markdown)

    # --- 1. Description (everything before the first ## or ### heading) ---
    intro_section = sections[0] if sections else ""
    # The intro is the text before the first ### or --- separator
    intro_parts = re.split(r'\n---\n|\n(?=### )', intro_section)
    if intro_parts:
        desc_text = intro_parts[0].strip()
        # Remove leading --- separators
        desc_text = re.sub(r'^---\s*', '', desc_text).strip()
        # Remove "show more" / "show less" noise
        desc_text = re.sub(r'\nshow more\s*\nshow less\s*', '', desc_text)
        desc_text = re.sub(r'\nshow more\s*$', '', desc_text)
        desc_text = re.sub(r'^show less\s*\n', '', desc_text)
        if desc_text and len(desc_text) > 30:
            result['description'] = desc_text

    # --- 2. Uses (### In {use_name} sections) ---
    uses_matches = re.findall(
        r'### In (?:Treatment of |Treatment Of )?(.+?)\n(.+?)(?=\n###|\n---|\n## |\Z)',
        markdown, re.DOTALL
    )
    if uses_matches:
        uses_parts = []
        for use_name, use_desc in uses_matches:
            clean_desc = use_desc.strip()
            clean_desc = re.sub(r'\nshow more\s*\nshow less\s*', '', clean_desc)
            clean_desc = re.sub(r'\nshow more\s*$', '', clean_desc)
            clean_desc = re.sub(r'^show less\s*\n', '', clean_desc)
            if clean_desc:
                uses_parts.append(f"{use_name.strip()}: {clean_desc}")
        if uses_parts:
            result['uses'] = '\n'.join(uses_parts)

    # --- 3. Side Effects ---
    # Side effects appear between uses and "How to use", often after a --- separator
    # Look for the side effects text pattern
    se_match = re.search(
        r'(?:side effects?.*?do not require.*?medical attention.*?\n|'
        r'Common side effects.*?\n)(.*?)(?=\n---|\n## )',
        markdown, re.DOTALL | re.IGNORECASE
    )
    if se_match:
        se_text = se_match.group(1).strip()
        # Extract bullet points
        bullets = re.findall(r'\*\s+(.+)', se_text)
        if bullets:
            se_clean = '\n'.join(b.strip() for b in bullets)
        elif se_text:
            se_clean = se_text
        else:
            se_clean = None
        if se_clean and se_clean != 'No common side effects seen':
            result['side_effects'] = se_clean
    else:
        # Try alternative: look for a section that lists side effects as bullets
        se_block = re.search(
            r'(?:Common side effects|Side effects).*?\n((?:\*\s+.+\n?)+)',
            markdown, re.IGNORECASE
        )
        if se_block:
            bullets = re.findall(r'\*\s+(.+)', se_block.group(1))
            if bullets:
                se_text = '\n'.join(b.strip() for b in bullets)
                if se_text and 'No common side effects' not in se_text:
                    result['side_effects'] = se_text

    # --- 4. How to Use ---
    for section in sections:
        if re.match(r'## How to use ', section, re.IGNORECASE):
            lines = section.split('\n', 1)
            if len(lines) > 1:
                content = lines[1].strip()
                content = re.sub(r'\n---\s*$', '', content).strip()
                if content and len(content) > 10:
                    result['how_to_use'] = content
            break

    # --- 5. How it Works ---
    for section in sections:
        if re.match(r'## How .+ works', section, re.IGNORECASE):
            lines = section.split('\n', 1)
            if len(lines) > 1:
                content = lines[1].strip()
                content = re.sub(r'\n---\s*$', '', content).strip()
                if content and len(content) > 10:
                    result['how_it_works'] = content
            break

    # --- 6. Safety Advice ---
    for section in sections:
        if re.match(r'## Safety advice', section, re.IGNORECASE):
            safety = _parse_safety_advice(section)
            if safety:
                result['safety_advice'] = safety
            break

    # --- 7. FAQs ---
    faq_matches = re.findall(
        r'### (.+\?)\s*\n(.*?)(?=\n###|\n## |\n---|\Z)',
        markdown, re.DOTALL
    )
    faqs = []
    for question, answer in faq_matches:
        q = question.strip()
        a = answer.strip()
        # Skip non-FAQ questions
        if any(skip in q.lower() for skip in [
            'what are you using', 'how much was', 'please rate',
            'how do you take', 'disclaimer', 'what were the side'
        ]):
            continue
        a = re.sub(r'\nShow more\s*\nShow less\s*', '', a)
        a = re.sub(r'\nShow more\s*$', '', a)
        if q and a and len(a) > 15:
            faqs.append({'question': q, 'answer': a})
    if faqs:
        result['faq'] = faqs

    return result


def _parse_safety_advice(section_text: str) -> str | None:
    """
    Parse the Safety Advice section into a structured newline-separated string.
    Format: "Topic: STATUS - Detail"
    """
    # Known safety categories on 1mg
    categories = ['Alcohol', 'Pregnancy', 'Breast feeding', 'Breastfeeding',
                   'Driving', 'Kidney', 'Liver']

    # Strip the heading
    lines_raw = section_text.split('\n')
    lines = []
    for line in lines_raw:
        if line.startswith('## '):
            continue
        lines.append(line)

    text = '\n'.join(lines)
    parts = []

    for cat in categories:
        # Pattern: Category name on its own line, followed by STATUS, then detail text
        pattern = rf'(?:^|\n){re.escape(cat)}\s*\n\s*\n?(UNSAFE|SAFE(?:\s+IF\s+PRESCRIBED)?|CAUTION|CONSULT YOUR DOCTOR|SAFE)\s*\n\s*\n?(.*?)(?=\n(?:{"|".join(re.escape(c) for c in categories)})\s*\n|\n---|\Z)'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            status = match.group(1).strip()
            detail = match.group(2).strip()
            # Clean up multi-line detail
            detail = re.sub(r'\n(?:However,?\s*)', ' However, ', detail)
            detail = re.sub(r'\n+', ' ', detail).strip()
            if detail:
                parts.append(f"{cat}: {status} - {detail}")
            else:
                parts.append(f"{cat}: {status}")

    return '\n'.join(parts) if parts else None


def extract_medicine_name_from_title(title: str) -> str | None:
    """
    Extract medicine name from the 1mg page title.
    Title format: "Buy Dolo 650 Tablet Online: View Uses, Side Effects, Price, Substitutes | 1mg"
    """
    match = re.match(r'Buy\s+(.+?)\s+Online', title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None
