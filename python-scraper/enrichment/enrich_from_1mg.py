"""
enrich_from_1mg.py — Fetch clinical data from 1mg product pages via Jina Reader
and enrich the MongoDB medicines collection.

Usage:
    python enrich_from_1mg.py                  # Process all medicines missing 'uses'
    python enrich_from_1mg.py --limit 5        # Process only 5 medicines (for testing)
    python enrich_from_1mg.py --batch 500      # Process in batches of 500
    python enrich_from_1mg.py --delay 2.0      # Custom delay between requests (seconds)
    python enrich_from_1mg.py --force           # Re-fetch even if 'uses' already exists

Strategy:
    1. For each medicine missing clinical data, find its 1mg URL from the prices collection
    2. Fetch the page markdown via Jina Reader (r.jina.ai)
    3. Parse the markdown into structured fields
    4. Safety-check the parsed medicine name vs DB name
    5. $set update (non-destructive — only fills empty fields)
"""

import os
import re
import sys
import time
import argparse
import requests
from dotenv import load_dotenv
from pymongo import MongoClient
from parser import parse_1mg_markdown, extract_medicine_name_from_title

# ── Load .env from parent directory (shared with Node.js scripts) ──
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

# ── Safety Match (same logic as the Node.js version) ──
NOISE_RE = re.compile(
    r'\b(tablet|tablets|capsule|capsules|syrup|drop|drops|injection|'
    r'cream|gel|ointment|strip|of|mg|ml|gm|mcg)\b', re.IGNORECASE
)


def clean_name(name: str) -> str:
    """Normalize a medicine name for comparison."""
    name = NOISE_RE.sub(' ', name.lower())
    name = re.sub(r'[^a-z0-9]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def is_safe_match(db_name: str, fetched_name: str, db_salt: str = '') -> bool:
    """
    Verify the fetched medicine actually matches our DB record.
    Checks: brand word overlap + dosage number overlap + salt fallback.
    """
    clean_db = clean_name(db_name)
    clean_fetch = clean_name(fetched_name)
    clean_salt = re.sub(r'[^a-z0-9]', ' ', (db_salt or '').lower()).strip()

    # Number matching
    db_nums = re.findall(r'\d+', clean_db)
    fetch_nums = re.findall(r'\d+', clean_fetch)
    if db_nums and fetch_nums:
        if not any(n in fetch_nums for n in db_nums):
            return False

    # Brand word matching
    db_alpha = [w for w in re.sub(r'\d+', ' ', clean_db).split() if len(w) > 1]
    fetch_alpha = [w for w in re.sub(r'\d+', ' ', clean_fetch).split() if len(w) > 1]

    if db_alpha and fetch_alpha:
        brand = db_alpha[0]
        if any(brand in fw or fw in brand for fw in fetch_alpha):
            return True
    elif not db_alpha:
        return True

    # Salt fallback
    if clean_salt:
        salt_words = [w for w in clean_salt.split() if len(w) > 3]
        if salt_words and fetch_alpha:
            if any(sw in fw or fw in sw for sw in salt_words for fw in fetch_alpha):
                return True

    return False


def fetch_via_jina(url: str, timeout: int = 30) -> str | None:
    """Fetch a page's markdown content via Jina Reader."""
    jina_url = f"{JINA_BASE}{url}"
    try:
        resp = requests.get(jina_url, headers=JINA_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        else:
            return None
    except requests.RequestException as e:
        print(f"    [Jina Error] {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description="Enrich medicines from 1mg via Jina Reader")
    ap.add_argument('--limit', type=int, default=0, help='Max medicines to process (0 = all)')
    ap.add_argument('--batch', type=int, default=0, help='Batch size (0 = no batching)')
    ap.add_argument('--delay', type=float, default=1.5, help='Delay between requests in seconds')
    ap.add_argument('--force', action='store_true', help='Re-fetch even if uses already exists')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N medicines (for resume)')
    args = ap.parse_args()

    # ── Connect to MongoDB ──
    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']
    prices = db['prices']

    print(f"Connected to MongoDB (MEDSAVE)")

    # ── Build query for medicines needing enrichment ──
    if args.force:
        query = {"normalized_name": {"$exists": True, "$ne": ""}}
    else:
        # Prioritize medicines missing 'uses' (which is 100% missing)
        query = {
            "normalized_name": {"$exists": True, "$ne": ""},
            "$or": [
                {"uses": {"$exists": False}},
                {"uses": ""},
            ]
        }

    total = medicines.count_documents(query)
    effective_limit = args.limit if args.limit > 0 else total
    print(f"Found {total} medicines needing enrichment. Will process up to {effective_limit}.")

    # ── Pre-build a lookup: medicine_id → 1mg URL ──
    print("Building 1mg URL index from prices collection...")
    onemg_urls = {}
    for price_doc in prices.find({"platform": "1mg", "url": {"$exists": True, "$ne": ""}}):
        med_id = price_doc.get("medicine_id")
        if med_id:
            onemg_urls[str(med_id)] = price_doc["url"]
    print(f"  Indexed {len(onemg_urls)} 1mg URLs.")

    # ── Process medicines ──
    cursor = medicines.find(query).skip(args.offset)
    processed = 0
    updated = 0
    skipped_no_url = 0
    skipped_safety = 0
    skipped_no_data = 0
    errors = 0

    for doc in cursor:
        if processed >= effective_limit:
            break
        processed += 1

        med_id = str(doc['_id'])
        name = doc.get('normalized_name', '')
        db_name = doc.get('name', name)
        db_salt = doc.get('salt', '')

        # Find 1mg URL
        onemg_url = onemg_urls.get(med_id)
        if not onemg_url:
            print(f"[{processed}/{effective_limit}] {name} — No 1mg URL, skipping")
            skipped_no_url += 1
            continue

        print(f"[{processed}/{effective_limit}] {name}")
        print(f"    URL: {onemg_url}")

        # Fetch via Jina
        markdown = fetch_via_jina(onemg_url)
        if not markdown:
            print(f"    [SKIP] Jina returned empty/error")
            errors += 1
            time.sleep(args.delay)
            continue

        # Check for 404/error pages
        if 'returned error 404' in markdown or "can't seem to find the page" in markdown.lower():
            print(f"    [SKIP] Page returned 404")
            errors += 1
            time.sleep(args.delay)
            continue

        # Extract medicine name from the title for safety check
        title_match = re.search(r'Title:\s*(.+)', markdown)
        fetched_title = title_match.group(1).strip() if title_match else ""
        fetched_name = extract_medicine_name_from_title(fetched_title) or ""

        if fetched_name and not is_safe_match(db_name, fetched_name, db_salt):
            print(f"    [Safety Reject] DB: \"{db_name}\" vs Fetched: \"{fetched_name}\"")
            skipped_safety += 1
            time.sleep(args.delay)
            continue

        # Parse the markdown
        parsed = parse_1mg_markdown(markdown)

        if not parsed:
            print(f"    [SKIP] Parser returned no data")
            skipped_no_data += 1
            time.sleep(args.delay)
            continue

        # Build $set update (non-destructive: only set fields that are missing)
        update_fields = {}
        for field in ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice']:
            if field in parsed and parsed[field]:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update_fields[field] = parsed[field]

        # FAQ: merge if we have new ones
        if 'faq' in parsed and parsed['faq']:
            existing_faq = doc.get('faq', [])
            if not existing_faq:
                update_fields['faq'] = parsed['faq']

        if update_fields:
            medicines.update_one({"_id": doc["_id"]}, {"$set": update_fields})
            fields_list = ', '.join(update_fields.keys())
            print(f"    [OK] Updated: {fields_list}")
            updated += 1
        else:
            print(f"    [SKIP] No new data to add")
            skipped_no_data += 1

        # Polite delay
        time.sleep(args.delay)

        # Batch checkpoint
        if args.batch > 0 and processed % args.batch == 0:
            print(f"\n--- Batch checkpoint at {processed}/{effective_limit} ---")
            print(f"    Updated: {updated}, No URL: {skipped_no_url}, "
                  f"Safety rejects: {skipped_safety}, Errors: {errors}\n")

    # ── Final Stats ──
    print(f"\n{'='*60}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'='*60}")
    print(f"  Processed:       {processed}")
    print(f"  Updated:         {updated}")
    print(f"  No 1mg URL:      {skipped_no_url}")
    print(f"  Safety rejects:  {skipped_safety}")
    print(f"  No new data:     {skipped_no_data}")
    print(f"  Errors:          {errors}")
    print(f"{'='*60}")

    client.close()


if __name__ == '__main__':
    main()
