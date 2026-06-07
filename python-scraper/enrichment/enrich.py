"""
enrich.py — Independent multi-platform clinical data enrichment pipeline.

Scrapes clinical data directly from pharmacy websites using Python.
No MedCompare, no Jina, no third-party APIs.

Pipeline Phases:
    Phase 1: Scrape from 1mg (14,211 URLs from prices collection)
    Phase 2: Copy clinical data to same-salt sibling medicines
    Phase 3: Scrape remaining from Netmeds
    Phase 4: Final sibling pass

Usage:
    python enrich.py --phase 1              # Run phase 1 (1mg scraping)
    python enrich.py --phase 2              # Run phase 2 (sibling copy)
    python enrich.py --phase 3              # Run phase 3 (Netmeds scraping)
    python enrich.py --phase 4              # Run phase 4 (final sibling pass)
    python enrich.py --phase all            # Run all phases sequentially
    python enrich.py --phase 1 --limit 10   # Test with 10 medicines
    python enrich.py --phase 1 --offset 500 # Resume from #500
    python enrich.py --phase 1 --force      # Force overwrite existing data
    python enrich.py --status               # Show current enrichment status
"""

import os
import re
import sys
import time
import argparse
from dotenv import load_dotenv
from pymongo import MongoClient

# ── Load .env ──
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

# Clinical fields we enrich
CLINICAL_FIELDS = ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice', 'faq']

# Safety matching
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

    clean_salt = re.sub(r'[^a-z0-9]', ' ', (db_salt or '').lower()).strip()
    if clean_salt:
        salt_words = [w for w in clean_salt.split() if len(w) > 3]
        if salt_words and fetch_alpha:
            if any(sw in fw or fw in sw for sw in salt_words for fw in fetch_alpha):
                return True

    return False


def build_update(doc, parsed: dict, force: bool = False) -> dict:
    """Build $set update from parsed data. If force=True, overwrite existing data."""
    update = {}
    for field in CLINICAL_FIELDS:
        if field in parsed and parsed[field]:
            if force:
                update[field] = parsed[field]
            else:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update[field] = parsed[field]
                elif isinstance(existing, list) and len(existing) == 0:
                    update[field] = parsed[field]
    return update


# ════════════════════════════════════════════════════════════
# PHASE 1: Scrape from 1mg
# ════════════════════════════════════════════════════════════
def phase_1_onemg(medicines, prices, limit, offset, delay, force=False):
    from scrapers.onemg import OneMGScraper

    print("=" * 60)
    mode = "FORCE MODE (overwrite all)" if force else "(skip existing)"
    print(f"PHASE 1: Direct 1mg Scraping {mode}")
    print("=" * 60)

    # Build 1mg URL index from prices collection
    print("\nBuilding 1mg URL index...")
    onemg_urls = {}
    for p in prices.find({"platform": "1mg", "url": {"$exists": True, "$ne": ""}}):
        mid = p.get("medicine_id")
        if mid:
            onemg_urls[str(mid)] = p["url"]
    print(f"  {len(onemg_urls)} 1mg URLs indexed")

    # Find medicines to process
    if force:
        # Force mode: process ALL medicines that have a 1mg URL
        query = {"normalized_name": {"$exists": True, "$ne": ""}}
    else:
        # Normal mode: only process medicines missing clinical data
        query = {
            "normalized_name": {"$exists": True, "$ne": ""},
            "$or": [{"uses": {"$exists": False}}, {"uses": ""}, {"uses": None}]
        }
    total = medicines.count_documents(query)
    effective_limit = limit if limit > 0 else total
    print(f"  {total} medicines to process, processing up to {effective_limit}\n")

    stats = {'processed': 0, 'updated': 0, 'skipped': 0, 'errors': 0, 'no_url': 0}

    with OneMGScraper(delay=delay) as scraper:
        cursor = medicines.find(query).skip(offset)

        for doc in cursor:
            if stats['processed'] >= effective_limit:
                break
            stats['processed'] += 1

            name = doc.get('normalized_name', '')
            db_name = doc.get('name', name)
            db_salt = (doc.get('salt') or '')
            med_id = str(doc['_id'])

            url = onemg_urls.get(med_id)
            if not url:
                stats['no_url'] += 1
                continue

            print(f"[{stats['processed']}/{effective_limit}] {name}")

            parsed = scraper.scrape_url(url)
            if not parsed:
                print(f"    [SKIP] No data / 404")
                stats['errors'] += 1
                continue

            # Safety check
            fetched_name = parsed.get('medicine_name', '')
            if fetched_name and not is_safe_match(db_name, fetched_name, db_salt):
                print(f"    [Safety Reject] DB: \"{db_name}\" vs 1mg: \"{fetched_name}\"")
                stats['skipped'] += 1
                continue

            update = build_update(doc, parsed, force=force)
            if update:
                update['clinical_data_source'] = '1mg'
                medicines.update_one({"_id": doc["_id"]}, {"$set": update})
                fields = ', '.join(k for k in update if k != 'clinical_data_source')
                print(f"    [OK] {fields}")
                stats['updated'] += 1
            else:
                stats['skipped'] += 1

            if stats['processed'] % 500 == 0:
                print(f"\n  --- Checkpoint: {stats['processed']}/{effective_limit}, updated: {stats['updated']} ---\n")

    print(f"\n{'='*60}")
    print(f"PHASE 1 COMPLETE: {stats['updated']} updated, {stats['errors']} errors, {stats['no_url']} no URL")
    print(f"{'='*60}\n")
    return stats


# ════════════════════════════════════════════════════════════
# PHASE 2 & 4: Sibling enrichment
# ════════════════════════════════════════════════════════════
def phase_siblings(medicines, phase_label="PHASE 2"):
    print("=" * 60)
    print(f"{phase_label}: Salt-Sibling Enrichment (No API calls)")
    print("=" * 60)

    # Build salt -> enriched doc index
    print("\nBuilding salt index from enriched medicines...")
    salt_index = {}
    for doc in medicines.find({"salt": {"$exists": True, "$ne": ""}, "uses": {"$exists": True, "$ne": ""}}):
        salt = (doc.get('salt') or '').strip().lower()
        if salt and salt not in salt_index:
            clinical = {}
            for field in CLINICAL_FIELDS:
                val = doc.get(field)
                if val and (isinstance(val, str) and val.strip() or isinstance(val, list) and len(val) > 0):
                    clinical[field] = val
            if clinical:
                salt_index[salt] = {
                    'name': doc.get('name', doc.get('normalized_name', '')),
                    'clinical': clinical
                }

    print(f"  {len(salt_index)} unique salts with enriched data")

    query = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "salt": {"$exists": True, "$ne": ""},
        "$or": [{"uses": {"$exists": False}}, {"uses": ""}, {"uses": None}]
    }
    total = medicines.count_documents(query)
    print(f"  {total} medicines still missing data\n")

    stats = {'processed': 0, 'updated': 0, 'no_match': 0}

    for doc in medicines.find(query):
        stats['processed'] += 1
        salt = (doc.get('salt') or '').strip().lower()
        if not salt:
            stats['no_match'] += 1
            continue

        sibling = salt_index.get(salt)
        if not sibling:
            stats['no_match'] += 1
            continue

        update = {}
        for field in CLINICAL_FIELDS:
            if field in sibling['clinical']:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update[field] = sibling['clinical'][field]
                elif isinstance(existing, list) and len(existing) == 0:
                    update[field] = sibling['clinical'][field]

        if update:
            update['clinical_data_source'] = 'salt_sibling'
            update['clinical_data_sibling'] = sibling['name']
            medicines.update_one({"_id": doc["_id"]}, {"$set": update})
            stats['updated'] += 1

        if stats['processed'] % 1000 == 0:
            print(f"  ... {stats['processed']}/{total}, updated: {stats['updated']}")

    print(f"\n{'='*60}")
    print(f"{phase_label} COMPLETE: {stats['updated']} updated from siblings, {stats['no_match']} no salt match")
    print(f"{'='*60}\n")
    return stats


# ════════════════════════════════════════════════════════════
# PHASE 3: Scrape from Netmeds
# ════════════════════════════════════════════════════════════
def phase_3_netmeds(medicines, prices, limit, offset, delay):
    from scrapers.netmeds import NetmedsScraper

    print("=" * 60)
    print("PHASE 3: Direct Netmeds Scraping (requests + BeautifulSoup)")
    print("=" * 60)

    query = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "$or": [{"uses": {"$exists": False}}, {"uses": ""}, {"uses": None}]
    }
    total = medicines.count_documents(query)
    effective_limit = limit if limit > 0 else total
    print(f"  {total} medicines still need enrichment, processing up to {effective_limit}\n")

    stats = {'processed': 0, 'updated': 0, 'not_found': 0}

    with NetmedsScraper(delay=delay) as scraper:
        cursor = medicines.find(query).skip(offset)

        for doc in cursor:
            if stats['processed'] >= effective_limit:
                break
            stats['processed'] += 1

            name = doc.get('normalized_name', '')
            db_salt = (doc.get('salt') or '')

            print(f"[{stats['processed']}/{effective_limit}] {name}")

            # Try by medicine name first
            parsed = scraper.scrape_by_name(name)

            # If not found, try by salt name
            if not parsed and db_salt:
                primary_salt = re.split(r'[\(\+,]', db_salt)[0].strip()
                if primary_salt and len(primary_salt) > 3:
                    parsed = scraper.scrape_by_name(primary_salt)

            if not parsed:
                print(f"    [SKIP] No data")
                stats['not_found'] += 1
                continue

            update = build_update(doc, parsed)
            if update:
                update['clinical_data_source'] = 'netmeds'
                medicines.update_one({"_id": doc["_id"]}, {"$set": update})
                fields = ', '.join(k for k in update if k != 'clinical_data_source')
                print(f"    [OK] {fields}")
                stats['updated'] += 1
            else:
                stats['not_found'] += 1

            if stats['processed'] % 200 == 0:
                print(f"\n  --- Checkpoint: {stats['processed']}/{effective_limit}, updated: {stats['updated']} ---\n")

    print(f"\n{'='*60}")
    print(f"PHASE 3 COMPLETE: {stats['updated']} updated, {stats['not_found']} not found")
    print(f"{'='*60}\n")
    return stats


# ════════════════════════════════════════════════════════════
# STATUS CHECK
# ════════════════════════════════════════════════════════════
def show_status(medicines):
    total = medicines.count_documents({})
    has_uses = medicines.count_documents({"uses": {"$exists": True, "$nin": ["", None]}})
    has_safety = medicines.count_documents({"safety_advice": {"$exists": True, "$nin": ["", None]}})
    has_desc = medicines.count_documents({"description": {"$exists": True, "$nin": ["", None]}})
    has_faq = medicines.count_documents({"faq": {"$exists": True, "$not": {"$size": 0}}})

    # By source
    via_1mg = medicines.count_documents({"clinical_data_source": "1mg"})
    via_sibling = medicines.count_documents({"clinical_data_source": "salt_sibling"})
    via_netmeds = medicines.count_documents({"clinical_data_source": "netmeds"})

    pct = round(has_uses / total * 100, 1) if total else 0

    print("=" * 60)
    print("ENRICHMENT STATUS")
    print("=" * 60)
    print(f"  Total medicines:      {total}")
    print(f"  Has uses:             {has_uses} ({pct}%)")
    print(f"  Has safety_advice:    {has_safety}")
    print(f"  Has description:      {has_desc}")
    print(f"  Has FAQ:              {has_faq}")
    print(f"  ---")
    print(f"  Via 1mg:              {via_1mg}")
    print(f"  Via salt sibling:     {via_sibling}")
    print(f"  Via Netmeds:          {via_netmeds}")
    print(f"  ---")
    print(f"  Remaining:            {total - has_uses}")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="Independent multi-platform enrichment pipeline")
    ap.add_argument('--phase', type=str, default='1', help='Phase to run: 1, 2, 3, 4, or all')
    ap.add_argument('--limit', type=int, default=0, help='Max medicines (0 = all)')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N')
    ap.add_argument('--delay', type=float, default=1.5, help='Delay between requests')
    ap.add_argument('--force', action='store_true', help='Force overwrite existing data')
    ap.add_argument('--status', action='store_true', help='Show enrichment status')
    args = ap.parse_args()

    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']
    prices = db['prices']

    if args.status:
        show_status(medicines)
        client.close()
        return

    phases = args.phase.split(',') if args.phase != 'all' else ['1', '2', '3', '4']

    for phase in phases:
        phase = phase.strip()
        if phase == '1':
            phase_1_onemg(medicines, prices, args.limit, args.offset, args.delay, force=args.force)
        elif phase == '2':
            phase_siblings(medicines, "PHASE 2")
        elif phase == '3':
            phase_3_netmeds(medicines, prices, args.limit, args.offset, args.delay)
        elif phase == '4':
            phase_siblings(medicines, "PHASE 4 (Final)")
        else:
            print(f"Unknown phase: {phase}")

    # Show final status
    show_status(medicines)
    client.close()


if __name__ == '__main__':
    main()
