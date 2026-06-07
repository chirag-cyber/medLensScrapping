"""
enrich_remaining.py — Final pass: enriches the remaining ~1,500 medicines that
1mg + siblings couldn't cover.

Strategy for the uncovered medicines:
    1. Try Netmeds /prescriptions/{slug} via Jina (construct slug from medicine name)
    2. If Netmeds fails, try 1mg search-by-salt via Jina
    3. Uses aggressive salt-based sibling matching as last resort

This script does NOT depend on MedCompare at all.

Usage:
    python enrich_remaining.py              # Process all remaining
    python enrich_remaining.py --limit 10   # Test with 10
    python enrich_remaining.py --delay 2.0  # Custom delay
"""

import os
import re
import sys
import time
import argparse
import requests
from dotenv import load_dotenv
from pymongo import MongoClient
from parser import parse_1mg_markdown
from parser_netmeds import parse_netmeds_markdown

# ── Load .env ──
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

JINA_BASE = "https://r.jina.ai/"
JINA_HEADERS = {
    "Accept": "text/markdown",
    "User-Agent": "Mozilla/5.0 (MediSaathi Enrichment Bot)"
}


def fetch_via_jina(url: str, timeout: int = 45) -> str | None:
    """Fetch markdown via Jina Reader."""
    try:
        resp = requests.get(f"{JINA_BASE}{url}", headers=JINA_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        return None
    except requests.RequestException:
        return None


def is_error_page(md: str) -> bool:
    lower = md.lower()
    return ('returned error 404' in md or
            "can't seem to find" in lower or
            'page not found' in lower or
            ('oops' in lower and 'broken' in lower))


def name_to_slug(name: str) -> str:
    """Convert medicine name to URL slug: 'Dolo 650mg Tablet 15s' -> 'dolo-650mg-tablet-15-s'"""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s]", '', slug)
    slug = re.sub(r'\s+', '-', slug)
    return slug


def try_1mg_salt_search(salt: str) -> dict | None:
    """Try to find clinical data by searching 1mg for the salt composition."""
    if not salt or len(salt) < 3:
        return None

    # Extract the primary salt name (first word before dosage)
    primary = re.split(r'[\(\+,]', salt)[0].strip()
    if not primary or len(primary) < 3:
        return None

    slug = name_to_slug(primary)
    url = f"https://www.1mg.com/drugs/{slug}"

    md = fetch_via_jina(url)
    if md and not is_error_page(md):
        parsed = parse_1mg_markdown(md)
        if parsed and parsed.get('uses'):
            return parsed

    return None


def try_netmeds_search(name: str, salt: str = '') -> dict | None:
    """Try to find clinical data on Netmeds prescriptions page."""
    slug = name_to_slug(name)
    url = f"https://www.netmeds.com/prescriptions/{slug}"

    md = fetch_via_jina(url)
    if md and not is_error_page(md):
        parsed = parse_netmeds_markdown(md)
        if parsed and (parsed.get('uses') or parsed.get('description')):
            return parsed

    # Try with salt name if brand didn't work
    if salt:
        primary = re.split(r'[\(\+,]', salt)[0].strip()
        if primary and len(primary) > 3:
            salt_slug = name_to_slug(primary)
            if salt_slug != slug:  # Don't retry same URL
                url = f"https://www.netmeds.com/prescriptions/{salt_slug}"
                md = fetch_via_jina(url)
                if md and not is_error_page(md):
                    parsed = parse_netmeds_markdown(md)
                    if parsed and (parsed.get('uses') or parsed.get('description')):
                        return parsed

    return None


def main():
    ap = argparse.ArgumentParser(description="Final pass: enrich remaining medicines via direct scraping")
    ap.add_argument('--limit', type=int, default=0, help='Max to process (0 = all)')
    ap.add_argument('--delay', type=float, default=2.0, help='Delay between requests')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N')
    args = ap.parse_args()

    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']

    print("=" * 60)
    print("FINAL PASS: Direct Pharmacy Scraping")
    print("Sources: 1mg salt search + Netmeds prescriptions")
    print("No MedCompare dependency!")
    print("=" * 60)

    query = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "uses": {"$exists": False}
    }
    # Also include medicines where uses is empty string or None
    query2 = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "uses": {"$in": ["", None]}
    }

    total = medicines.count_documents({"$or": [query, query2]})
    effective_limit = args.limit if args.limit > 0 else total
    print(f"Found {total} medicines still missing clinical data.")
    print(f"Processing up to {effective_limit}.\n")

    cursor = medicines.find({"$or": [query, query2]}).skip(args.offset)
    stats = {
        'processed': 0, 'updated': 0, 'not_found': 0, 'errors': 0,
        'via_netmeds': 0, 'via_1mg_salt': 0
    }

    CLINICAL_FIELDS = ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice', 'faq']

    for doc in cursor:
        if stats['processed'] >= effective_limit:
            break
        stats['processed'] += 1

        name = doc.get('normalized_name', '')
        db_salt = (doc.get('salt') or '')

        print(f"[{stats['processed']}/{effective_limit}] {name}")

        parsed = None
        source = None

        # Strategy 1: Try Netmeds prescriptions page
        parsed = try_netmeds_search(name, db_salt)
        if parsed:
            source = 'netmeds_direct'
        else:
            time.sleep(args.delay * 0.5)  # Short delay between attempts

            # Strategy 2: Try 1mg salt search
            parsed = try_1mg_salt_search(db_salt)
            if parsed:
                source = '1mg_salt_search'

        if not parsed:
            print(f"    [SKIP] No data from any source")
            stats['not_found'] += 1
            time.sleep(args.delay)
            continue

        # Build non-destructive update
        update_fields = {}
        for field in CLINICAL_FIELDS:
            if field in parsed and parsed[field]:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update_fields[field] = parsed[field]
                elif isinstance(existing, list) and len(existing) == 0:
                    update_fields[field] = parsed[field]

        if update_fields:
            update_fields['clinical_data_source'] = source
            medicines.update_one({"_id": doc["_id"]}, {"$set": update_fields})
            fields_list = ', '.join(k for k in update_fields.keys() if k != 'clinical_data_source')
            print(f"    [OK] ({source}): {fields_list}")
            stats['updated'] += 1
            if source == 'netmeds_direct':
                stats['via_netmeds'] += 1
            else:
                stats['via_1mg_salt'] += 1
        else:
            print(f"    [SKIP] No new data")
            stats['not_found'] += 1

        time.sleep(args.delay)

        if stats['processed'] % 200 == 0:
            _print_stats(stats, effective_limit, True)

    _print_stats(stats, effective_limit, False)
    client.close()


def _print_stats(stats, total, checkpoint=False):
    label = "CHECKPOINT" if checkpoint else "COMPLETE"
    print(f"\n{'='*60}")
    print(f"FINAL PASS {label}")
    print(f"{'='*60}")
    print(f"  Processed:       {stats['processed']}/{total}")
    print(f"  Updated:         {stats['updated']}")
    print(f"    via Netmeds:   {stats['via_netmeds']}")
    print(f"    via 1mg salt:  {stats['via_1mg_salt']}")
    print(f"  Not found:       {stats['not_found']}")
    print(f"  Errors:          {stats['errors']}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
