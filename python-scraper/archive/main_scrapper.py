import asyncio
import logging
import sys
import json
from search_engine import UnifiedSearchEngine, pick_best_per_platform
from detail_scraper import ClinicalDetailScraper

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def full_medicine_scrapper(query: str):
    """
    Search for a medicine across all platforms and fetch 
    comprehensive clinical + pricing data.
    
    Output schema:
    {
      "query": "...",
      "clinical_info": { ... comprehensive clinical data ... },
      "platforms": [ ... per-platform pricing ... ]
    }
    """
    engine = UnifiedSearchEngine()
    clinical_fetcher = ClinicalDetailScraper(delay=0.5)
    
    print(f"\n{'='*60}")
    print(f"--- STARTING FULL SCRAPE FOR: '{query}' ---")
    print(f"{'='*60}\n")
    
    # 1. Search across all platforms
    logger.info("Step 1: Searching for medicine across all platforms...")
    all_results = await engine.search_all_async(query)
    
    if not all_results:
        print("!!! No results found on any platform.")
        return

    # 2. Smart filtering: pick BEST matching result per platform
    logger.info("Step 2: Filtering results by relevance...")
    best_matches = pick_best_per_platform(query, all_results)
    
    if not best_matches:
        print("!!! No relevant results matched your query.")
        return

    # Sort by price (cheapest first)
    best_matches.sort(key=lambda x: x['sale_price'] if x['sale_price'] > 0 else 99999)
    
    print(f"\nFound relevant matches on {len(best_matches)} platforms. Fetching clinical details...\n")

    # 3. Fetch comprehensive clinical data
    # Priority: 1mg URL > Netmeds URL > composition from API platforms
    onemg_url = None
    netmeds_url = None
    api_composition = None
    
    for med in best_matches:
        platform = med['platform']
        if platform == '1mg':
            onemg_url = med['url']
        elif platform == 'netmeds':
            netmeds_url = med['url']
        elif platform in ('truemeds', 'platinumrx') and med.get('composition'):
            api_composition = med['composition']
    
    logger.info("Step 3: Fetching comprehensive clinical data...")
    clinical_info = clinical_fetcher.fetch_clinical_data(
        onemg_url=onemg_url, 
        netmeds_url=netmeds_url
    )
    
    # Fill composition from API if clinical scraper didn't get it
    if not clinical_info.get('composition') and api_composition:
        clinical_info['composition'] = api_composition
    
    # 4. Build per-platform pricing records
    platforms = []
    for med in best_matches:
        platform_record = {
            "platform": med['platform'].upper(),
            "name": med['name'].replace('\u20b9', 'Rs.').replace('₹', 'Rs.'),
            "price": med['sale_price'],
            "mrp": med['mrp'],
            "discount_percent": med.get('discount_percent', 0.0),
            "pack_size": med.get('pack_size', ''),
            "url": med['url'],
            "manufacturer": med.get('manufacturer', ''),
            "in_stock": med.get('in_stock', True)
        }
        platforms.append(platform_record)
    
    # 5. Build final output
    result = {
        "query": query,
        "clinical_info": clinical_info,
        "platforms": platforms
    }
    
    # 6. Output Results
    print(f"\n{'='*60}")
    print(f"--- FINAL SCRAPE RESULTS FOR: '{query}' ---")
    print(f"{'='*60}\n")
    
    # Clinical summary
    ci = clinical_info
    print(f"[CLINICAL INFO]")
    print(f"   Composition: {ci.get('composition', 'N/A')}")
    print(f"   Manufacturer: {ci.get('manufacturer', 'N/A')}")
    print(f"   Rx Status: {ci.get('prescription_status', 'N/A')}")
    if ci.get('uses'):
        print(f"   Uses: {', '.join(ci['uses'][:3])}...")
    if ci.get('side_effects'):
        se_list = [s for s in ci['side_effects'] if len(s) < 60][:3]
        print(f"   Side Effects: {', '.join(se_list)}")
    if ci.get('safety_advice'):
        sa_keys = list(ci['safety_advice'].keys())
        sa_statuses = [f"{k}={ci['safety_advice'][k].get('status','?')}" for k in sa_keys[:4]]
        print(f"   Safety: {', '.join(sa_statuses)}")
    if ci.get('quick_tips'):
        print(f"   Quick Tips: {len(ci['quick_tips'])} tips")
    if ci.get('drug_interactions'):
        print(f"   Drug Interactions: {len(ci['drug_interactions'])} listed")
    if ci.get('substitutes'):
        sub_names = [s['name'] for s in ci['substitutes'][:3]]
        print(f"   Substitutes: {', '.join(sub_names)}...")
    if ci.get('faqs'):
        print(f"   FAQs: {len(ci['faqs'])} questions")
    print(f"   Fact Box: {ci.get('fact_box', {})}")
    
    # Pricing comparison
    print(f"\n[PRICE COMPARISON] ({len(platforms)} platforms):")
    for i, p in enumerate(platforms):
        discount_str = f" (-{p['discount_percent']:.0f}%)" if p.get('discount_percent', 0) > 0 else ""
        stock_str = "" if p.get('in_stock', True) else " [OUT OF STOCK]"
        print(f"   {i+1}. [{p['platform']}] {p['name']}")
        print(f"      Price: Rs.{p['price']}{discount_str} (MRP: Rs.{p['mrp']}){stock_str}")
        print(f"      URL: {p['url']}")

    # Save to file
    with open('scraped_medicine.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("Full data saved to scraped_medicine.json")
    
    clinical_fetcher.close()

if __name__ == "__main__":
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    asyncio.run(full_medicine_scrapper(query))
