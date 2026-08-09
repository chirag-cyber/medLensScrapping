"""
enrich_clinical_v2.py — Safe, comprehensive clinical data enrichment from 1mg.

What it does:
  1. Iterates through medicines in MongoDB
  2. Looks up the 1mg URL from the prices collection (already indexed)
  3. Fetches comprehensive clinical data via ClinicalDetailScraper
  4. Applies NON-DESTRUCTIVE $set updates (only fills empty/missing fields)
  5. CLEANS UP malformed existing data (string->list, string->dict fixes)
  6. Logs everything with stats and checkpoint summaries

Safety:
  - --dry-run mode to preview without DB writes
  - Batch processing with checkpoints
  - Safety matching (verify scraped medicine matches DB record)
  - Never overwrites good existing data (unless --force)
  - Full audit log

Usage:
  python enrich_clinical_v2.py --dry-run --limit 10     # Preview 10
  python enrich_clinical_v2.py --limit 50               # Small test batch
  python enrich_clinical_v2.py --batch 100 --delay 1.5  # Production run
  python enrich_clinical_v2.py --offset 500 --batch 100 # Resume from #500
  python enrich_clinical_v2.py --force --limit 5         # Force re-enrich
  python enrich_clinical_v2.py --fix-only --limit 100   # Fix malformed only
"""

import os
import re
import sys
import time
import json
import argparse
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient
from detail_scraper import ClinicalDetailScraper

# ── Setup ──
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("enrich_clinical_v2.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ── Safety matching ──
NOISE_RE = re.compile(
    r'\b(tablet|tablets|capsule|capsules|syrup|drop|drops|injection|'
    r'cream|gel|ointment|strip|of|mg|ml|gm|mcg|sr|er|cr|xr)\b', re.IGNORECASE
)

def clean_name(name: str) -> str:
    name = NOISE_RE.sub(' ', name.lower())
    name = re.sub(r'[^a-z0-9]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()

def is_safe_match(db_name: str, scraped_name: str, db_salt: str = '') -> bool:
    """Verify the scraped medicine matches the DB record."""
    clean_db = clean_name(db_name)
    clean_scraped = clean_name(scraped_name)
    clean_salt = re.sub(r'[^a-z0-9]', ' ', (db_salt or '').lower()).strip()

    if not clean_db or not clean_scraped:
        return True  # Can't verify, allow

    # Number matching
    db_nums = re.findall(r'\d+', clean_db)
    scraped_nums = re.findall(r'\d+', clean_scraped)
    if db_nums and scraped_nums:
        if not any(n in scraped_nums for n in db_nums):
            return False

    # Brand word matching
    db_alpha = [w for w in re.sub(r'\d+', ' ', clean_db).split() if len(w) > 1]
    scraped_alpha = [w for w in re.sub(r'\d+', ' ', clean_scraped).split() if len(w) > 1]

    if db_alpha and scraped_alpha:
        brand = db_alpha[0]
        if any(brand in fw or fw in brand for fw in scraped_alpha):
            return True

    # Salt fallback
    if clean_salt:
        salt_words = [w for w in clean_salt.split() if len(w) > 3]
        if salt_words and scraped_alpha:
            if any(sw in fw or fw in sw for sw in salt_words for fw in scraped_alpha):
                return True

    return False


# ================================================================
#  MALFORMED DATA CLEANUP
# ================================================================

def fix_malformed_uses(current_uses) -> list | None:
    """Fix uses stored as newline-separated string -> list."""
    if isinstance(current_uses, str) and current_uses.strip():
        # Split on newlines, clean up
        items = [line.strip() for line in current_uses.split('\n') if line.strip()]
        # Remove "show more/show less" artifacts
        items = [re.sub(r'show\s*more\s*show\s*less', '', i, flags=re.I).strip() for i in items]
        items = [i for i in items if i and len(i) > 2]
        return items if items else None
    return None

def fix_malformed_safety_advice(current_sa) -> dict | None:
    """Fix safety_advice stored as string -> structured dict."""
    if not isinstance(current_sa, str) or not current_sa.strip():
        return None

    safety = {}
    text = current_sa

    # Parse patterns like: "Alcohol UNSAFE\nIt is unsafe..."
    categories = {
        'alcohol': ['alcohol'],
        'pregnancy': ['pregnancy', 'pregnant'],
        'breastfeeding': ['breast feeding', 'breastfeeding', 'breast-feeding'],
        'driving': ['driving'],
        'kidney': ['kidney'],
        'liver': ['liver']
    }

    # Split by category boundaries
    lines = text.split('\n')
    current_cat = None
    current_parts = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if this line starts a new category
        found_cat = None
        for cat, keywords in categories.items():
            for kw in keywords:
                if line.lower().startswith(kw):
                    found_cat = cat
                    break
            if found_cat:
                break

        if found_cat:
            # Save previous category
            if current_cat and current_parts:
                full_text = ' '.join(current_parts)
                status = "UNKNOWN"
                if 'unsafe' in full_text.lower():
                    status = "UNSAFE"
                elif 'safe' in full_text.lower() and 'unsafe' not in full_text.lower():
                    status = "SAFE"
                elif 'caution' in full_text.lower() or 'consult' in full_text.lower():
                    status = "CAUTION"
                safety[current_cat] = {"status": status, "text": full_text}

            # Start new category
            current_cat = found_cat
            # Remove the category label from the line
            for kw in categories[found_cat]:
                line = re.sub(rf'^{re.escape(kw)}\s*', '', line, flags=re.I)
            # Also remove status words from beginning
            line = re.sub(r'^(UNSAFE|SAFE|CAUTION|CONSULT YOUR DOCTOR)\s*', '', line, flags=re.I).strip()
            current_parts = [line] if line else []
        elif current_cat:
            current_parts.append(line)

    # Save last category
    if current_cat and current_parts:
        full_text = ' '.join(current_parts)
        status = "UNKNOWN"
        if 'unsafe' in full_text.lower():
            status = "UNSAFE"
        elif 'safe' in full_text.lower() and 'unsafe' not in full_text.lower():
            status = "SAFE"
        elif 'caution' in full_text.lower() or 'consult' in full_text.lower():
            status = "CAUTION"
        safety[current_cat] = {"status": status, "text": full_text}

    return safety if safety else None

def fix_malformed_side_effects(current_se) -> list | None:
    """Fix side_effects stored as string -> list."""
    if isinstance(current_se, str) and current_se.strip():
        # Try splitting by common delimiters
        text = current_se.strip()
        # Remove prefix noise
        text = re.sub(r'^Most side effects do not require.*?body adjusts to the medicine\.?\s*', '', text, flags=re.I | re.S)
        text = re.sub(r'Consult your doctor if .*$', '', text, flags=re.I)

        # Split by newlines, commas, or bullet-like patterns
        items = re.split(r'[\n,]|\s*[\u2022\u25CF\u25CB•]\s*', text)
        items = [i.strip().strip('.').strip() for i in items if i.strip()]
        items = [i for i in items if len(i) > 2 and len(i) < 80]

        return items if items else None
    return None

def clean_description(desc: str) -> str | None:
    """Clean promotional junk from descriptions."""
    if not isinstance(desc, str) or not desc.strip():
        return None

    original = desc
    # Remove promotional patterns
    desc = re.sub(r'Buy\s+\w+.*?(?:online|cashback|discount).*?(?:\.|$)', '', desc, flags=re.I)
    desc = re.sub(r'(?:Order|Get)\s+(?:now|online).*?(?:\.|$)', '', desc, flags=re.I)
    desc = re.sub(r'(?:flat|extra)\s+\d+%\s+(?:off|cashback|discount).*?(?:\.|$)', '', desc, flags=re.I)
    desc = re.sub(r'(?:use\s+code\s+\w+).*?(?:\.|$)', '', desc, flags=re.I)
    desc = re.sub(r'(?:free\s+delivery|free\s+shipping).*?(?:\.|$)', '', desc, flags=re.I)

    desc = desc.strip()
    if desc != original and len(desc) > 20:
        return desc
    return None


# ================================================================
#  MAIN ENRICHMENT LOGIC
# ================================================================

def build_update_fields(doc: dict, clinical: dict, force: bool = False) -> dict:
    """
    Build the $set update dict. Non-destructive: only fills empty/missing fields.
    Also fixes malformed existing data.
    """
    update = {}

    # ── New fields (always set if data available — they don't exist yet) ──
    new_fields = ['quick_tips', 'substitutes', 'drug_interactions', 'fact_box',
                  'missed_dose', 'prescription_status', 'dosage_form', 'administration_route']
    for field in new_fields:
        val = clinical.get(field)
        if val:  # Only if non-empty
            existing = doc.get(field)
            if not existing or force:
                update[field] = val

    # ── Existing fields: fill only if missing/empty OR fix malformed ──

    # Uses
    current_uses = doc.get('uses')
    new_uses = clinical.get('uses', [])
    if isinstance(current_uses, str) and current_uses.strip():
        # FIX: Convert string -> list
        fixed = fix_malformed_uses(current_uses)
        if fixed:
            update['uses'] = fixed
    elif not current_uses or (isinstance(current_uses, list) and len(current_uses) == 0):
        if new_uses:
            update['uses'] = new_uses
    elif force and new_uses:
        update['uses'] = new_uses

    # Safety Advice
    current_sa = doc.get('safety_advice')
    new_sa = clinical.get('safety_advice', {})
    if isinstance(current_sa, str) and current_sa.strip():
        # FIX: Convert string -> dict
        fixed = fix_malformed_safety_advice(current_sa)
        if fixed:
            update['safety_advice'] = fixed
        elif new_sa:
            update['safety_advice'] = new_sa
    elif not current_sa or (isinstance(current_sa, dict) and len(current_sa) == 0):
        if new_sa:
            update['safety_advice'] = new_sa
    elif force and new_sa:
        update['safety_advice'] = new_sa

    # Side Effects
    current_se = doc.get('side_effects')
    new_se = clinical.get('side_effects', [])
    if isinstance(current_se, str):
        # FIX: Convert string -> list
        fixed = fix_malformed_side_effects(current_se)
        if fixed:
            update['side_effects'] = fixed
        elif new_se:
            update['side_effects'] = new_se
    elif isinstance(current_se, list) and len(current_se) == 0:
        if new_se:
            update['side_effects'] = new_se
    elif force and new_se:
        update['side_effects'] = new_se

    # How to Use
    current_htu = doc.get('how_to_use')
    if not current_htu or (isinstance(current_htu, str) and current_htu.strip() == '') or force:
        if clinical.get('how_to_use'):
            update['how_to_use'] = clinical['how_to_use']

    # How it Works
    current_hiw = doc.get('how_it_works')
    if not current_hiw or (isinstance(current_hiw, str) and current_hiw.strip() == '') or force:
        if clinical.get('how_it_works'):
            update['how_it_works'] = clinical['how_it_works']

    # Description (only if missing/junk)
    current_desc = doc.get('description')
    if not current_desc or (isinstance(current_desc, str) and len(current_desc.strip()) < 20):
        if clinical.get('description'):
            update['description'] = clinical['description']
    elif isinstance(current_desc, str):
        cleaned = clean_description(current_desc)
        if cleaned:
            update['description'] = cleaned

    # Salt/Composition (only if missing)
    if not doc.get('salt') and clinical.get('composition'):
        update['salt'] = clinical['composition']

    # Manufacturer (only if missing)
    if not doc.get('manufacturer') and clinical.get('manufacturer'):
        update['manufacturer'] = clinical['manufacturer']

    # FAQ (only if new has more)
    current_faq = doc.get('faq', [])
    new_faq = clinical.get('faqs', [])
    if isinstance(current_faq, list) and isinstance(new_faq, list):
        if len(new_faq) > len(current_faq):
            update['faq'] = new_faq

    return update


def main():
    ap = argparse.ArgumentParser(description="Safe clinical data enrichment from 1mg")
    ap.add_argument('--limit', type=int, default=0, help='Max medicines (0 = all)')
    ap.add_argument('--batch', type=int, default=0, help='Batch checkpoint size')
    ap.add_argument('--delay', type=float, default=1.5, help='Delay between requests (seconds)')
    ap.add_argument('--offset', type=int, default=0, help='Skip first N medicines')
    ap.add_argument('--force', action='store_true', help='Overwrite existing data')
    ap.add_argument('--dry-run', action='store_true', help='Preview updates without writing')
    ap.add_argument('--fix-only', action='store_true', help='Only fix malformed data, no new scraping')
    args = ap.parse_args()

    # ── Connect ──
    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']
    prices = db['prices']

    print("=" * 60)
    print("CLINICAL DATA ENRICHMENT v2")
    if args.dry_run:
        print(">>> DRY RUN MODE — NO DB WRITES <<<")
    if args.fix_only:
        print(">>> FIX-ONLY MODE — NO SCRAPING <<<")
    print("=" * 60)

    # ── Build 1mg URL index from prices ──
    logger.info("Building 1mg URL index from prices collection...")
    onemg_urls = {}
    for pdoc in prices.find({'platform': '1mg', 'url': {'$exists': True, '$ne': ''}},
                            {'medicine_id': 1, 'url': 1}):
        med_id = pdoc.get('medicine_id')
        if med_id:
            onemg_urls[str(med_id)] = pdoc['url']
    logger.info(f"  Indexed {len(onemg_urls)} 1mg URLs")

    # Also build netmeds URL index for fallback
    netmeds_urls = {}
    for pdoc in prices.find({'platform': 'netmeds', 'url': {'$exists': True, '$ne': ''}},
                            {'medicine_id': 1, 'url': 1}):
        med_id = pdoc.get('medicine_id')
        if med_id:
            netmeds_urls[str(med_id)] = pdoc['url']
    logger.info(f"  Indexed {len(netmeds_urls)} netmeds URLs")

    # ── Build query ──
    # Key principle: skip medicines already processed by v2 (have clinical_enriched_at)
    # unless --force is used
    not_already_done = {'clinical_enriched_at': {'$exists': False}}

    if args.fix_only:
        # Only find documents with malformed data types
        query = {
            '$or': [
                {'uses': {'$type': 'string'}},
                {'safety_advice': {'$type': 'string'}},
                {'side_effects': {'$type': 'string'}},
            ]
        }
    elif args.force:
        query = {'normalized_name': {'$exists': True, '$ne': ''}}
    else:
        # Find medicines that need enrichment or have malformed data
        # BUT skip ones already enriched by v2
        #
        # Manufacturer recovery: records whose manufacturer was blanked by
        # heal_manufacturer.py (a platform self-name like "Apollo Pharmacy" was
        # removed) must be re-selected here so the real maker can be refetched
        # from the 1mg/netmeds detail JSON-LD (marketer.legalName). These records
        # are otherwise fully enriched (they carry clinical_enriched_at), so the
        # not_already_done gate would skip them. Put the blank-manufacturer clause
        # OUTSIDE that gate as a top-level OR so it is always eligible.
        query = {
            '$or': [
                # Manufacturer needs recovery (blank or missing) — always eligible,
                # even if clinical fields are already enriched.
                {'manufacturer': ''},
                {'manufacturer': {'$exists': False}},
                {'$and': [
                    not_already_done,
                    {'$or': [
                        # Missing new fields
                        {'quick_tips': {'$exists': False}},
                        # Missing existing fields
                        {'uses': {'$exists': False}},
                        {'uses': ''},
                        # Malformed data
                        {'uses': {'$type': 'string'}},
                        {'safety_advice': {'$type': 'string'}},
                        {'side_effects': {'$type': 'string'}},
                    ]}
                ]}
            ]
        }

    total = medicines.count_documents(query)
    effective_limit = args.limit if args.limit > 0 else total
    logger.info(f"Found {total} medicines needing work. Processing up to {effective_limit}.")

    # ── Initialize scraper ──
    scraper = ClinicalDetailScraper(delay=0.5) if not args.fix_only else None

    # ── Process ──
    cursor = medicines.find(query).skip(args.offset)
    stats = {
        'processed': 0, 'updated': 0, 'fixed_malformed': 0,
        'no_url': 0, 'safety_reject': 0, 'no_data': 0, 'errors': 0,
        'skipped': 0
    }

    for doc in cursor:
        if stats['processed'] >= effective_limit:
            break
        stats['processed'] += 1

        med_id = str(doc['_id'])
        name = doc.get('name', doc.get('normalized_name', 'Unknown'))
        db_salt = doc.get('salt', '')

        # ── Fix-only mode: just fix malformed data, no scraping ──
        if args.fix_only:
            update = {}
            if isinstance(doc.get('uses'), str):
                fixed = fix_malformed_uses(doc['uses'])
                if fixed:
                    update['uses'] = fixed
            if isinstance(doc.get('safety_advice'), str):
                fixed = fix_malformed_safety_advice(doc['safety_advice'])
                if fixed:
                    update['safety_advice'] = fixed
            if isinstance(doc.get('side_effects'), str):
                fixed = fix_malformed_side_effects(doc['side_effects'])
                if fixed:
                    update['side_effects'] = fixed

            if update:
                fields = ', '.join(update.keys())
                if args.dry_run:
                    print(f"[{stats['processed']}/{effective_limit}] {name} -- WOULD FIX: {fields}")
                    for k, v in update.items():
                        print(f"    {k}: {str(v)[:100]}")
                else:
                    medicines.update_one({'_id': doc['_id']}, {'$set': update})
                    print(f"[{stats['processed']}/{effective_limit}] {name} -- FIXED: {fields}")
                stats['fixed_malformed'] += 1
            else:
                stats['skipped'] += 1
            continue

        # ── Normal mode: scrape + enrich ──
        onemg_url = onemg_urls.get(med_id)
        netmeds_url = netmeds_urls.get(med_id)

        if not onemg_url and not netmeds_url:
            stats['no_url'] += 1
            # Mark as attempted so we don't re-check on every run
            if not args.dry_run:
                medicines.update_one({'_id': doc['_id']}, {'$set': {
                    'clinical_enriched_at': datetime.now(timezone.utc),
                    'clinical_enrich_status': 'no_url'
                }})
            if stats['no_url'] <= 5:
                logger.debug(f"[{stats['processed']}] {name} -- No URL, skipping")
            continue

        try:
            # Fetch clinical data
            clinical = scraper.fetch_clinical_data(
                onemg_url=onemg_url,
                netmeds_url=netmeds_url
            )

            if not clinical or (not clinical.get('description') and not clinical.get('uses')):
                stats['no_data'] += 1
                # Mark as attempted
                if not args.dry_run:
                    medicines.update_one({'_id': doc['_id']}, {'$set': {
                        'clinical_enriched_at': datetime.now(timezone.utc),
                        'clinical_enrich_status': 'no_data'
                    }})
                if stats['no_data'] <= 5:
                    logger.warning(f"[{stats['processed']}] {name} -- No clinical data returned")
                time.sleep(args.delay)
                continue

            # Safety check: verify scraped medicine matches DB record
            # Since the URL comes from our own prices collection (already mapped by medicine_id),
            # we trust the URL is correct. We only reject if dosage numbers clearly mismatch.
            scraped_composition = clinical.get('composition', '')
            if scraped_composition and db_salt:
                # Extract dosage numbers from both — normalize to float for comparison
                db_dose_strs = re.findall(r'(\d+(?:\.\d+)?)\s*(?:mg|ml|mcg|gm)', db_salt.lower())
                scraped_dose_strs = re.findall(r'(\d+(?:\.\d+)?)\s*(?:mg|ml|mcg|gm)', scraped_composition.lower())
                db_doses = set(float(d) for d in db_dose_strs)
                scraped_doses = set(float(d) for d in scraped_dose_strs)
                
                if db_doses and scraped_doses and not db_doses.intersection(scraped_doses):
                    # Dosage numbers completely different — likely wrong medicine
                    stats['safety_reject'] += 1
                    if not args.dry_run:
                        medicines.update_one({'_id': doc['_id']}, {'$set': {
                            'clinical_enriched_at': datetime.now(timezone.utc),
                            'clinical_enrich_status': 'safety_reject'
                        }})
                    logger.warning(f"[{stats['processed']}] {name} -- Dosage mismatch: DB={db_doses} vs scraped={scraped_doses}")
                    time.sleep(args.delay)
                    continue

            # Build non-destructive update
            update = build_update_fields(doc, clinical, force=args.force)

            if update:
                # Add provenance metadata
                update['clinical_data_source'] = '1mg' if onemg_url else 'netmeds'
                update['clinical_enriched_at'] = datetime.now(timezone.utc)
                update['clinical_enrich_status'] = 'success'

                fields = [k for k in update.keys() if k not in ('clinical_data_source', 'clinical_enriched_at', 'clinical_enrich_status')]

                if args.dry_run:
                    print(f"[{stats['processed']}/{effective_limit}] {name}")
                    print(f"    WOULD UPDATE: {', '.join(fields)}")
                    for k in fields[:4]:
                        val = update[k]
                        print(f"    {k}: {str(val)[:80]}")
                else:
                    medicines.update_one({'_id': doc['_id']}, {'$set': update})
                    logger.info(f"[{stats['processed']}/{effective_limit}] {name} -- Updated: {', '.join(fields)}")

                stats['updated'] += 1

                # Check if we also fixed malformed data
                if any(isinstance(doc.get(f), str) for f in ['uses', 'safety_advice', 'side_effects'] if f in update):
                    stats['fixed_malformed'] += 1
            else:
                stats['skipped'] += 1

        except Exception as e:
            stats['errors'] += 1
            logger.error(f"[{stats['processed']}] {name} -- Error: {e}")

        # Polite delay
        time.sleep(args.delay)

        # Batch checkpoint
        if args.batch > 0 and stats['processed'] % args.batch == 0:
            _print_stats(stats, effective_limit, checkpoint=True)

    # ── Final stats ──
    _print_stats(stats, effective_limit, checkpoint=False)

    if scraper:
        scraper.close()
    client.close()


def _print_stats(stats, total, checkpoint=False):
    label = "BATCH CHECKPOINT" if checkpoint else "ENRICHMENT COMPLETE"
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Processed:       {stats['processed']}/{total}")
    print(f"  Updated:         {stats['updated']}")
    print(f"  Fixed malformed: {stats['fixed_malformed']}")
    print(f"  No URL:          {stats['no_url']}")
    print(f"  Safety rejects:  {stats['safety_reject']}")
    print(f"  No data:         {stats['no_data']}")
    print(f"  Skipped (ok):    {stats['skipped']}")
    print(f"  Errors:          {stats['errors']}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
