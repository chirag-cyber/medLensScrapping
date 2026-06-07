"""
search_medicine.py — Search any medicine across pharmacy websites.

Works for medicines NOT in the database. Future-proof.

Usage:
    python search_medicine.py "Dolo 650"
    python search_medicine.py "Azithromycin 500mg" --json
    python search_medicine.py "Metformin 500" --save   # Also save to DB
"""

import sys
import json
import argparse
from scrapers.onemg import OneMGScraper
from scrapers.netmeds import NetmedsScraper


def search(name: str, verbose: bool = True) -> dict | None:
    """
    Search for a medicine across all supported platforms.

    Strategy:
        1. Check DB for stored URLs (from prices collection)
        2. Try 1mg with the stored URL
        3. Try Netmeds with the stored URL
        4. Fall back to slug-based URL construction

    Returns the best clinical data found, or None.
    """
    import os
    from dotenv import load_dotenv
    from pymongo import MongoClient

    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
    mongo_url = os.getenv('MONGO_URL')

    # Step 1: Try to find URLs from our DB
    onemg_url = None
    netmeds_url = None

    if mongo_url:
        try:
            client = MongoClient(mongo_url)
            db = client['MEDSAVE']

            # Find medicine in DB by name
            med = db['medicines'].find_one({
                "$or": [
                    {"normalized_name": {"$regex": name.lower().strip(), "$options": "i"}},
                    {"name": {"$regex": name.strip(), "$options": "i"}},
                ]
            })

            if med:
                med_id = med['_id']
                if verbose:
                    print(f"Found in DB: {med.get('name', '?')}")

                # Get stored URLs
                for price in db['prices'].find({"medicine_id": med_id}):
                    platform = price.get('platform', '')
                    url = price.get('url', '')
                    if platform == '1mg' and url:
                        onemg_url = url
                    elif platform == 'netmeds' and url:
                        netmeds_url = url

                if verbose:
                    if onemg_url:
                        print(f"  1mg URL: {onemg_url}")
                    if netmeds_url:
                        print(f"  Netmeds URL: {netmeds_url}")
            client.close()
        except Exception as e:
            if verbose:
                print(f"  DB lookup failed: {e}")

    # Step 2: Scrape using stored URLs or slug-based construction
    result = None

    # Try 1mg
    if verbose:
        print(f"\nSearching 1mg...")
    with OneMGScraper(delay=0.5) as scraper:
        if onemg_url:
            result = scraper.scrape_url(onemg_url)
        if not result:
            result = scraper.scrape_by_name(name)

    if result and result.get('uses'):
        if verbose:
            print(f"  [1mg] Found clinical data!")
        result['source'] = '1mg'
        return result

    # Try Netmeds
    if verbose:
        print(f"Searching Netmeds...")
    with NetmedsScraper(delay=0.5) as scraper:
        if netmeds_url:
            result = scraper.scrape_url(netmeds_url)
        if not result:
            result = scraper.scrape_by_name(name)

    if result and (result.get('uses') or result.get('description')):
        if verbose:
            print(f"  [Netmeds] Found clinical data!")
        result['source'] = 'netmeds'
        return result

    if verbose:
        print(f"  No clinical data found on any platform.")
    return None


def main():
    ap = argparse.ArgumentParser(description="Search any medicine across pharmacy websites")
    ap.add_argument('name', type=str, help='Medicine name to search')
    ap.add_argument('--json', action='store_true', help='Output as JSON')
    ap.add_argument('--save', action='store_true', help='Save to MongoDB')
    args = ap.parse_args()

    result = search(args.name, verbose=not args.json)

    if not result:
        if args.json:
            print(json.dumps({"error": "not_found"}, indent=2))
        else:
            print("\nNo clinical data found.")
        sys.exit(1)

    if args.json:
        # Clean up for JSON output
        output = {}
        for k, v in result.items():
            if v and (isinstance(v, str) and v.strip() or isinstance(v, list)):
                output[k] = v
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"Medicine: {result.get('medicine_name', args.name)}")
        print(f"Source: {result.get('source', 'unknown')}")
        print(f"{'='*60}")

        for field in ['description', 'uses', 'side_effects', 'how_to_use', 'how_it_works', 'safety_advice']:
            val = result.get(field)
            if val:
                print(f"\n--- {field.replace('_', ' ').title()} ---")
                print(val[:500])

        faq = result.get('faq')
        if faq:
            print(f"\n--- FAQs ({len(faq)} questions) ---")
            for qa in faq[:3]:
                print(f"  Q: {qa['question'][:80]}")
                print(f"  A: {qa['answer'][:120]}")
                print()

    if args.save:
        import os
        from dotenv import load_dotenv
        from pymongo import MongoClient

        load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
        client = MongoClient(os.getenv('MONGO_URL'))
        db = client['MEDSAVE']
        # Save logic here (optional — implement when needed)
        print("\n[Save to DB not yet implemented — use enrich.py for DB updates]")
        client.close()


if __name__ == '__main__':
    main()
