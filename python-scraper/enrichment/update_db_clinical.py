import asyncio
import logging
import os
from pymongo import MongoClient
from dotenv import load_dotenv
from detail_scraper import ClinicalDetailScraper
from search_engine import UnifiedSearchEngine

# Load environment variables
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
client = MongoClient(MONGO_URL)
db = client['MEDSAVE']
medicines_col = db['medicines']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def update_missing_clinical_data():
    """
    Find medicines with missing or messy clinical data and update them 
    using the new high-quality scrapper.
    """
    # 1. Find medicines that need update (missing side_effects or salt)
    query = {
        "$or": [
            {"side_effects": {"$exists": False}},
            {"side_effects": []},
            {"side_effects": ""},
            {"salt": {"$exists": False}},
            {"salt": ""}
        ]
    }
    
    # We'll take a small batch first for testing
    medicines = list(medicines_col.find(query).limit(10))
    logger.info(f"Found {len(medicines)} medicines needing clinical data update.")

    if not medicines:
        logger.info("No medicines found needing updates.")
        return

    engine = UnifiedSearchEngine()
    detail_fetcher = ClinicalDetailScraper()

    for med in medicines:
        med_name = med.get('name', 'Unknown')
        med_id = med.get('_id')
        
        logger.info(f"--- Processing: {med_name} ---")
        
        # Step A: Search strategy - Try specific name, then broad name if needed
        search_results = await engine.search_all_async(med_name)
        
        if not search_results:
            # Try a broader search (first two words)
            broad_name = " ".join(med_name.split()[:2])
            logger.info(f"Retrying broad search for: {broad_name}")
            search_results = await engine.search_all_async(broad_name)
        
        best_url = None
        best_platform = None
        
        # Priority: 1mg > Apollo > PharmEasy > Netmeds
        priority = ['1mg', 'apollo', 'pharmeasy', 'netmeds']
        for p in priority:
            for res in search_results:
                if res['platform'] == p:
                    best_url = res['url']
                    best_platform = p
                    break
            if best_url: break
        
        if not best_url:
            logger.warning(f"Could not find ANY platform URL for {med_name}. Skipping.")
            continue

        logger.info(f"Found match on [{best_platform}]: {best_url}")

        # Step B: Fetch detailed clinical info from the best platform found
        try:
            details = {
                "composition": "N/A",
                "side_effects": [],
                "safety_advice": {}
            }
            
            if best_platform == '1mg':
                details = await detail_fetcher.fetch_onemg_details(best_url)
            elif best_platform == 'netmeds':
                details = await detail_fetcher.fetch_netmeds_details(best_url)
            # Add other detail fetchers as needed
            
            # Step C: Update MongoDB
            update_fields = {}
            if details.get('composition') and details['composition'] != "N/A":
                update_fields['salt'] = details['composition']
            if details.get('side_effects'):
                update_fields['side_effects'] = details['side_effects']
            if details.get('safety_advice'):
                update_fields['safety_advice'] = details['safety_advice']
            
            if update_fields:
                medicines_col.update_one({"_id": med_id}, {"$set": update_fields})
                logger.info(f"Successfully updated {med_name} using {best_platform}")
            else:
                logger.warning(f"No clinical data found on {best_platform} for {med_name}")
                
        except Exception as e:
            logger.error(f"Error updating {med_name}: {e}")

    await detail_fetcher.stop()
    logger.info("Update process completed.")

if __name__ == "__main__":
    asyncio.run(update_missing_clinical_data())
