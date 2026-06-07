"""
enrich_from_medcompare.py — Fetch clinical data from MedCompare's medicine-info API.

Uses the /api/medicine-info endpoint (NOT multi-search which is rate-limited).
This API returns: description, uses, side_effects, how_to_use, how_it_works, safety_advice.

For medicines already enriched by 1mg/siblings, this script skips them.
Handles 404s (brand not found) with salt-based fallback queries.

Usage:
    python enrich_from_medcompare.py              # Process all missing
    python enrich_from_medcompare.py --limit 10   # Test with 10
    python enrich_from_medcompare.py --delay 1.5  # Custom delay
"""

import os
import re
import sys
import time
import json
import argparse
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

# ── Load .env ──
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

# MedCompare medicine-info API (separate from multi-search, no rate limit issues)
MEDCOMPARE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3YTk0ZjRjZC0zZDI1LTQxNjgtOTcwOS01NmRkYjJiNDlkM2MiLCJlbWFpbCI6IndvcmtpbmcyODIyQGdtYWlsLmNvbSIsImV4cCI6MTc3OTM3MTQ0NiwiaWF0IjoxNzc4NzY2NjQ2fQ.9h56D3Cc_mlWsKsVka1XgFoyLRnQtNPrUEtqsTpqFHc"
MEDCOMPARE_INFO_URL = "https://www.medcompare.in/api/medicine-info"

HEADERS = {
    "Authorization": f"Bearer {MEDCOMPARE_TOKEN}",
    "User-Agent": "Mozilla/5.0 (MediSaathi Enrichment Bot)"
}

# Fields to extract from MedCompare
CLINICAL_FIELDS = ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice']

# Safety matching (reused from the other scripts)
NOISE_RE = re.compile(
    r'\b(tablet|tablets|capsule|capsules|syrup|drop|drops|injection|'
    r'cream|gel|ointment|strip|of|mg|ml|gm|mcg)\b', re.IGNORECASE
)


def clean_name(name: str) -> str:
    name = NOISE_RE.sub(' ', name.lower())
    name = re.sub(r'[^a-z0-9]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def is_safe_match(db_name: str, fetched_name: str, db_salt: str = '') -> bool:
    clean_db = clean_name(db_name)
    clean_fetch = clean_name(fetched_name)
    clean_salt = re.sub(r'[^a-z0-9]', ' ', (db_salt or '').lower()).strip()

    db_nums = re.findall(r'\d+', clean_db)
    fetch_nums = re.findall(r'\d+', clean_fetch)
    if db_nums and fetch_nums:
        if not any(n in fetch_nums for n in db_nums):
            return False

    db_alpha = [w for w in re.sub(r'\d+', ' ', clean_db).split() if len(w) > 1]
    fetch_alpha = [w for w in re.sub(r'\d+', ' ', clean_fetch).split() if len(w) > 1]

    if db_alpha and fetch_alpha:
        brand = db_alpha[0]
        if any(brand in fw or fw in brand for fw in fetch_alpha):
            return True
    elif not db_alpha:
        return True

    if clean_salt:
        salt_words = [w for w in clean_salt.split() if len(w) > 3]
        if salt_words and fetch_alpha:
            if any(sw in fw or fw in sw for sw in salt_words for fw in fetch_alpha):
                return True

    return False


def fetch_medicine_info(query: str, timeout: int = 30) -> dict | None:
    """Fetch medicine info from MedCompare's medicine-info API."""
    try:
        resp = requests.get(
            MEDCOMPARE_INFO_URL,
            params={"q": query},
            headers=HEADERS,
            timeout=timeout
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            return None  # Medicine not found
        elif resp.status_code == 429:
            print(f"    [429] Rate limited!")
            return "RATE_LIMITED"
        else:
            print(f"    [HTTP {resp.status_code}]")
            return None
    except requests.RequestException as e:
        print(f"    [Error] {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description="Enrich from MedCompare medicine-info API")
    ap.add_argument('--limit', type=int, default=0, help='Max medicines to process (0 = all)')
    ap.add_argument('--delay', type=float, default=1.0, help='Delay between requests in seconds')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N medicines')
    args = ap.parse_args()

    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']

    print("=" * 60)
    print("MEDCOMPARE medicine-info ENRICHMENT")
    print("(Separate from multi-search — no rate limit issues)")
    print("=" * 60)

    # Find medicines still missing clinical data
    query = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "$or": [
            {"uses": {"$exists": False}},
            {"uses": ""},
            {"uses": None},
        ]
    }

    total = medicines.count_documents(query)
    effective_limit = args.limit if args.limit > 0 else total
    print(f"Found {total} medicines still missing clinical data.")
    print(f"Processing up to {effective_limit}.\n")

    cursor = medicines.find(query).skip(args.offset)
    stats = {
        'processed': 0, 'updated': 0, 'not_found': 0,
        'safety_reject': 0, 'no_data': 0, 'errors': 0, 'rate_limited': 0
    }

    for doc in cursor:
        if stats['processed'] >= effective_limit:
            break
        stats['processed'] += 1

        name = doc.get('normalized_name', '')
        db_name = doc.get('name', name)
        db_salt = (doc.get('salt') or '')
        primary_salt = doc.get('primary_salt_key', '')

        print(f"[{stats['processed']}/{effective_limit}] {name}")

        # Try brand name first
        info = fetch_medicine_info(name)

        # If 404, try with salt as fallback
        if info is None and (primary_salt or db_salt):
            fallback_q = primary_salt or db_salt.split(' ')[0]
            if fallback_q and fallback_q.strip():
                print(f"    [404] Trying salt fallback: {fallback_q}")
                info = fetch_medicine_info(fallback_q)

        if info == "RATE_LIMITED":
            stats['rate_limited'] += 1
            print(f"    Waiting 10s for rate limit...")
            time.sleep(10)
            # Retry once
            info = fetch_medicine_info(name)
            if info == "RATE_LIMITED":
                stats['rate_limited'] += 1
                time.sleep(30)
                continue

        if not info:
            stats['not_found'] += 1
            time.sleep(args.delay)
            continue

        # Safety check
        fetched_name = info.get('name', '') or info.get('brand_name', '') or ''
        if fetched_name and not is_safe_match(db_name, fetched_name, db_salt):
            print(f"    [Safety Reject] DB: \"{db_name}\" vs API: \"{fetched_name}\"")
            stats['safety_reject'] += 1
            time.sleep(args.delay)
            continue

        # Build non-destructive update
        update_fields = {}
        for field in CLINICAL_FIELDS:
            val = info.get(field)
            if val and isinstance(val, str) and val.strip():
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update_fields[field] = val.strip()

        # Also grab product_intro if we don't have description
        if 'description' not in update_fields:
            pi = info.get('product_intro', '')
            if pi and isinstance(pi, str) and pi.strip():
                existing_desc = doc.get('description')
                if not existing_desc or (isinstance(existing_desc, str) and existing_desc.strip() == ''):
                    update_fields['description'] = pi.strip()

        if update_fields:
            update_fields['clinical_data_source'] = 'medcompare'
            medicines.update_one({"_id": doc["_id"]}, {"$set": update_fields})
            fields_list = ', '.join(k for k in update_fields.keys() if k != 'clinical_data_source')
            print(f"    [OK] Updated: {fields_list}")
            stats['updated'] += 1
        else:
            print(f"    [SKIP] No new data")
            stats['no_data'] += 1

        time.sleep(args.delay)

        # Progress checkpoint
        if stats['processed'] % 500 == 0:
            _print_stats(stats, effective_limit, True)

    _print_stats(stats, effective_limit, False)
    client.close()


def _print_stats(stats, total, checkpoint=False):
    label = "CHECKPOINT" if checkpoint else "COMPLETE"
    print(f"\n{'='*60}")
    print(f"MEDCOMPARE ENRICHMENT {label}")
    print(f"{'='*60}")
    print(f"  Processed:       {stats['processed']}/{total}")
    print(f"  Updated:         {stats['updated']}")
    print(f"  Not found (404): {stats['not_found']}")
    print(f"  Safety rejects:  {stats['safety_reject']}")
    print(f"  No new data:     {stats['no_data']}")
    print(f"  Rate limited:    {stats['rate_limited']}")
    print(f"  Errors:          {stats['errors']}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
