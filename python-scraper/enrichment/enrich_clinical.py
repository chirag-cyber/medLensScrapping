"""
enrich_clinical.py — Multi-platform clinical data enrichment via Jina Reader.

Fetches clinical data from pharmacy websites (1mg, Netmeds) using Jina Reader
and enriches the MongoDB medicines collection.

Platform Priority:
    1. 1mg (best clinical data, 14,211 URLs)
    2. Netmeds (excellent fallback, covers ~2,800 more medicines)
    3. Apollo/PharmEasy return 404 via Jina — NOT supported

Usage:
    python enrich_clinical.py                  # Process all missing 'uses'
    python enrich_clinical.py --limit 5        # Test with 5 medicines
    python enrich_clinical.py --batch 500      # Batch with checkpoints
    python enrich_clinical.py --delay 2.0      # Custom delay (seconds)
    python enrich_clinical.py --force          # Re-fetch even if data exists
    python enrich_clinical.py --offset 500     # Resume from medicine #500
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
from parser_netmeds import parse_netmeds_markdown, extract_netmeds_medicine_name

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


def fetch_via_jina(url: str, timeout: int = 45) -> str | None:
    """Fetch a page's markdown content via Jina Reader."""
    jina_url = f"{JINA_BASE}{url}"
    try:
        resp = requests.get(jina_url, headers=JINA_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code == 429:
            print(f"    [Jina 429] Rate limited, will retry after longer delay")
            return "RATE_LIMITED"
        else:
            print(f"    [Jina {resp.status_code}] Unexpected response")
            return None
    except requests.RequestException as e:
        print(f"    [Jina Error] {e}")
        return None


def is_error_page(markdown: str) -> bool:
    """Check if the returned markdown is a 404 or error page."""
    lower = markdown.lower()
    return (
        'returned error 404' in markdown or
        "can't seem to find the page" in lower or
        "oops" in lower and "broken" in lower or
        "page not found" in lower
    )


def process_medicine(doc, url, platform, db_salt, db_name, delay):
    """
    Fetch and parse clinical data for a single medicine from the given platform URL.
    Returns (parsed_dict, source_platform) or (None, None).
    """
    markdown = fetch_via_jina(url)

    if markdown == "RATE_LIMITED":
        # Wait extra time on rate limit
        time.sleep(delay * 5)
        markdown = fetch_via_jina(url)

    if not markdown or markdown == "RATE_LIMITED":
        return None, None

    if is_error_page(markdown):
        return None, None

    # Extract title for safety check
    title_match = re.search(r'Title:\s*(.+)', markdown)
    fetched_title = title_match.group(1).strip() if title_match else ""

    # Parse based on platform
    if platform == '1mg':
        fetched_name = extract_medicine_name_from_title(fetched_title) or ""
        parsed = parse_1mg_markdown(markdown)
    elif platform == 'netmeds':
        fetched_name = extract_netmeds_medicine_name(fetched_title) or ""
        parsed = parse_netmeds_markdown(markdown)
    else:
        return None, None

    # Safety check
    if fetched_name and not is_safe_match(db_name, fetched_name, db_salt):
        print(f"    [Safety Reject] DB: \"{db_name}\" vs {platform}: \"{fetched_name}\"")
        return "SAFETY_REJECT", platform

    return parsed, platform


def main():
    ap = argparse.ArgumentParser(description="Multi-platform clinical data enrichment via Jina Reader")
    ap.add_argument('--limit', type=int, default=0, help='Max medicines to process (0 = all)')
    ap.add_argument('--batch', type=int, default=0, help='Batch size for checkpoints (0 = no batching)')
    ap.add_argument('--delay', type=float, default=1.5, help='Delay between requests in seconds')
    ap.add_argument('--force', action='store_true', help='Re-fetch even if uses already exists')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N medicines (for resume)')
    args = ap.parse_args()

    # ── Connect to MongoDB ──
    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']
    prices = db['prices']

    print("=" * 60)
    print("MULTI-PLATFORM CLINICAL DATA ENRICHMENT")
    print("Platforms: 1mg (primary) -> Netmeds (fallback)")
    print("=" * 60)
    print(f"Connected to MongoDB (MEDSAVE)")

    # ── Build query ──
    if args.force:
        query = {"normalized_name": {"$exists": True, "$ne": ""}}
    else:
        query = {
            "normalized_name": {"$exists": True, "$ne": ""},
            "$or": [
                {"uses": {"$exists": False}},
                {"uses": ""},
            ]
        }

    total = medicines.count_documents(query)
    effective_limit = args.limit if args.limit > 0 else total
    print(f"Found {total} medicines needing enrichment. Processing up to {effective_limit}.\n")

    # ── Pre-build URL indexes for all supported platforms ──
    print("Building URL indexes from prices collection...")
    platform_urls = {}

    # 1mg: URLs are direct product links (perfect as-is)
    onemg_urls = {}
    for price_doc in prices.find({"platform": "1mg", "url": {"$exists": True, "$ne": ""}}):
        med_id = price_doc.get("medicine_id")
        if med_id:
            onemg_urls[str(med_id)] = price_doc["url"]
    platform_urls['1mg'] = onemg_urls
    print(f"  1mg: {len(onemg_urls)} URLs indexed")

    # Netmeds: Convert /product/ URLs to /prescriptions/ URLs for clinical data
    # /product/dolo-650mg-tablet-15s-lui1wb-8231049  -->  /prescriptions/dolo-650mg-tablet-15-s
    # We derive the prescriptions slug from the product slug
    netmeds_urls = {}
    for price_doc in prices.find({"platform": "netmeds", "url": {"$exists": True, "$ne": ""}}):
        med_id = price_doc.get("medicine_id")
        url = price_doc.get("url", "")
        if med_id and url:
            # Try to convert to prescriptions URL
            # Extract the base slug (before the random ID suffix)
            import re as _re
            slug_match = _re.search(r'/(?:product|prescriptions)/([^/]+?)(?:-[a-z0-9]{6,}-\d+)?$', url)
            if slug_match:
                base_slug = slug_match.group(1)
                prescriptions_url = f"https://www.netmeds.com/prescriptions/{base_slug}"
                netmeds_urls[str(med_id)] = prescriptions_url
            else:
                # Keep original URL as fallback
                netmeds_urls[str(med_id)] = url
    platform_urls['netmeds'] = netmeds_urls
    print(f"  netmeds: {len(netmeds_urls)} URLs indexed (converted to /prescriptions/)")


    print()

    # ── Process medicines ──
    cursor = medicines.find(query).skip(args.offset)
    stats = {
        'processed': 0, 'updated': 0, 'no_url': 0,
        'safety_reject': 0, 'no_data': 0, 'errors': 0,
        'by_platform': {'1mg': 0, 'netmeds': 0}
    }

    for doc in cursor:
        if stats['processed'] >= effective_limit:
            break
        stats['processed'] += 1

        med_id = str(doc['_id'])
        name = doc.get('normalized_name', '')
        db_name = doc.get('name', name)
        db_salt = doc.get('salt', '')

        # Try platforms in priority order: 1mg -> netmeds
        source_url = None
        source_platform = None
        for platform in ['1mg', 'netmeds']:
            url = platform_urls[platform].get(med_id)
            if url:
                source_url = url
                source_platform = platform
                break

        if not source_url:
            print(f"[{stats['processed']}/{effective_limit}] {name} -- No URL on any platform, skipping")
            stats['no_url'] += 1
            continue

        print(f"[{stats['processed']}/{effective_limit}] {name}")
        print(f"    Source: {source_platform} | {source_url}")

        # Fetch and parse
        parsed, used_platform = process_medicine(doc, source_url, source_platform, db_salt, db_name, args.delay)

        if parsed == "SAFETY_REJECT":
            stats['safety_reject'] += 1
            time.sleep(args.delay)
            continue

        if not parsed:
            # If 1mg failed (404/error), try netmeds as fallback
            if source_platform == '1mg':
                netmeds_url = platform_urls['netmeds'].get(med_id)
                if netmeds_url:
                    print(f"    [1mg failed] Trying Netmeds fallback...")
                    print(f"    Fallback: netmeds | {netmeds_url}")
                    parsed, used_platform = process_medicine(doc, netmeds_url, 'netmeds', db_salt, db_name, args.delay)
                    if parsed == "SAFETY_REJECT":
                        stats['safety_reject'] += 1
                        time.sleep(args.delay)
                        continue

            if not parsed:
                print(f"    [SKIP] No data from any platform")
                stats['errors'] += 1
                time.sleep(args.delay)
                continue

        # Build $set update (non-destructive)
        update_fields = {}
        for field in ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice']:
            if field in parsed and parsed[field]:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update_fields[field] = parsed[field]

        # FAQ
        if 'faq' in parsed and parsed['faq']:
            existing_faq = doc.get('faq', [])
            if not existing_faq:
                update_fields['faq'] = parsed['faq']

        if update_fields:
            # Tag the source platform
            update_fields['clinical_data_source'] = used_platform or source_platform
            medicines.update_one({"_id": doc["_id"]}, {"$set": update_fields})
            fields_list = ', '.join(k for k in update_fields.keys() if k != 'clinical_data_source')
            print(f"    [OK] Updated ({used_platform or source_platform}): {fields_list}")
            stats['updated'] += 1
            stats['by_platform'][used_platform or source_platform] += 1
        else:
            print(f"    [SKIP] No new data to add")
            stats['no_data'] += 1

        # Polite delay
        time.sleep(args.delay)

        # Batch checkpoint
        if args.batch > 0 and stats['processed'] % args.batch == 0:
            _print_stats(stats, effective_limit, checkpoint=True)

    # ── Final Stats ──
    _print_stats(stats, effective_limit, checkpoint=False)
    client.close()


def _print_stats(stats, total, checkpoint=False):
    label = "BATCH CHECKPOINT" if checkpoint else "ENRICHMENT COMPLETE"
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"  Processed:       {stats['processed']}/{total}")
    print(f"  Updated:         {stats['updated']}")
    print(f"    via 1mg:       {stats['by_platform']['1mg']}")
    print(f"    via Netmeds:   {stats['by_platform']['netmeds']}")
    print(f"  No URL:          {stats['no_url']}")
    print(f"  Safety rejects:  {stats['safety_reject']}")
    print(f"  No new data:     {stats['no_data']}")
    print(f"  Errors/404s:     {stats['errors']}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
