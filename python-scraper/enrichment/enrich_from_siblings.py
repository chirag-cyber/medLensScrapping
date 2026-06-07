"""
enrich_from_siblings.py — Fill missing clinical data by copying from sibling medicines
with the same salt composition. No API calls needed!

Logic:
    1. Find all medicines still missing 'uses' (not enriched yet)
    2. For each, find a sibling medicine with the SAME salt that HAS been enriched
    3. Copy clinical fields from the sibling (uses, side_effects, how_to_use, etc.)
    4. Tag with clinical_data_source = 'salt_sibling'

This handles the ~3,900 medicines that have no 1mg URL but share salt compositions
with enriched medicines.

Usage:
    python enrich_from_siblings.py              # Process all
    python enrich_from_siblings.py --limit 10   # Test with 10
    python enrich_from_siblings.py --dry-run    # Preview without writing
"""

import os
import sys
import argparse
from dotenv import load_dotenv
from pymongo import MongoClient

# ── Load .env from parent directory ──
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

# Fields to copy from sibling
CLINICAL_FIELDS = ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice', 'faq']


def main():
    ap = argparse.ArgumentParser(description="Enrich medicines from salt-matched siblings (no API needed)")
    ap.add_argument('--limit', type=int, default=0, help='Max medicines to process (0 = all)')
    ap.add_argument('--dry-run', action='store_true', help='Preview without writing to DB')
    args = ap.parse_args()

    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']

    print("=" * 60)
    print("SALT-SIBLING ENRICHMENT (No API calls!)")
    print("=" * 60)

    # Step 1: Build a salt -> enriched_doc lookup
    # Find all medicines that HAVE 'uses' populated (already enriched)
    print("\nBuilding salt -> enriched medicine index...")
    salt_index = {}  # salt -> first enriched doc with that salt
    enriched_cursor = medicines.find({
        "salt": {"$exists": True, "$ne": ""},
        "uses": {"$exists": True, "$ne": ""}
    })

    enriched_count = 0
    for doc in enriched_cursor:
        salt = (doc.get('salt') or '').strip().lower()
        if salt and salt not in salt_index:
            # Store only the clinical fields to save memory
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
                enriched_count += 1

    print(f"  Found {enriched_count} unique salts with enriched data")

    # Step 2: Find medicines still missing clinical data
    missing_query = {
        "normalized_name": {"$exists": True, "$ne": ""},
        "salt": {"$exists": True, "$ne": ""},
        "$or": [
            {"uses": {"$exists": False}},
            {"uses": ""},
        ]
    }

    total_missing = medicines.count_documents(missing_query)
    effective_limit = args.limit if args.limit > 0 else total_missing
    print(f"  Found {total_missing} medicines still missing clinical data")
    print(f"  Will process up to {effective_limit}\n")

    if args.dry_run:
        print("  *** DRY RUN MODE — no changes will be written ***\n")

    # Step 3: For each missing medicine, find a sibling
    cursor = medicines.find(missing_query)
    stats = {
        'processed': 0, 'updated': 0, 'no_salt_match': 0, 'no_salt': 0, 'no_new_data': 0
    }

    for doc in cursor:
        if stats['processed'] >= effective_limit:
            break
        stats['processed'] += 1

        name = doc.get('normalized_name', doc.get('name', '???'))
        salt = (doc.get('salt') or '').strip().lower()

        if not salt:
            stats['no_salt'] += 1
            continue

        # Find sibling
        sibling = salt_index.get(salt)
        if not sibling:
            if stats['processed'] <= 20 or stats['processed'] % 500 == 0:
                print(f"[{stats['processed']}/{effective_limit}] {name} -- No sibling for salt: {salt[:50]}")
            stats['no_salt_match'] += 1
            continue

        # Build non-destructive update
        update_fields = {}
        for field in CLINICAL_FIELDS:
            if field in sibling['clinical']:
                existing = doc.get(field)
                if not existing or (isinstance(existing, str) and existing.strip() == ''):
                    update_fields[field] = sibling['clinical'][field]
                elif isinstance(existing, list) and len(existing) == 0:
                    update_fields[field] = sibling['clinical'][field]

        if update_fields:
            update_fields['clinical_data_source'] = 'salt_sibling'
            update_fields['clinical_data_sibling'] = sibling['name']

            if not args.dry_run:
                medicines.update_one({"_id": doc["_id"]}, {"$set": update_fields})

            fields_list = ', '.join(k for k in update_fields.keys()
                                    if k not in ('clinical_data_source', 'clinical_data_sibling'))

            if stats['updated'] < 20 or stats['updated'] % 200 == 0:
                print(f"[{stats['processed']}/{effective_limit}] {name}")
                print(f"    Sibling: {sibling['name']}")
                print(f"    [OK] Copied: {fields_list}")

            stats['updated'] += 1
        else:
            stats['no_new_data'] += 1

        # Progress indicator every 500
        if stats['processed'] % 500 == 0:
            print(f"  ... progress: {stats['processed']}/{effective_limit}, "
                  f"updated: {stats['updated']}, no match: {stats['no_salt_match']}")

    # Final stats
    print(f"\n{'='*60}")
    print(f"SIBLING ENRICHMENT {'(DRY RUN) ' if args.dry_run else ''}COMPLETE")
    print(f"{'='*60}")
    print(f"  Processed:        {stats['processed']}")
    print(f"  Updated:          {stats['updated']}")
    print(f"  No salt match:    {stats['no_salt_match']}")
    print(f"  No salt field:    {stats['no_salt']}")
    print(f"  No new data:      {stats['no_new_data']}")
    print(f"{'='*60}")

    client.close()


if __name__ == '__main__':
    main()
