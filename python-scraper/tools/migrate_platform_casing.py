import os
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load env variables
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

def migrate():
    if not MONGO_URL:
        logger.error("MONGO_URL not found in .env file.")
        return
        
    client = MongoClient(MONGO_URL)
    db = client.get_database("MEDSAVE")
    prices_col = db.get_collection("prices")
    
    # 1. Fetch all prices
    logger.info("Fetching all price entries from database...")
    all_prices = list(prices_col.find({}))
    logger.info(f"Found {len(all_prices)} price documents.")
    
    updated_count = 0
    deleted_count = 0
    seen_unique = set() # (medicine_id, platform_lowercase)
    
    for price in all_prices:
        price_id = price['_id']
        med_id = price.get('medicine_id')
        platform = price.get('platform')
        
        if not platform:
            continue
            
        platform_lower = platform.lower()
        unique_key = (str(med_id), platform_lower)
        
        # Check if we've already seen/saved a price entry for this medicine + platform combination
        if unique_key in seen_unique:
            # Duplicate detected! Delete this redundant price entry
            prices_col.delete_one({"_id": price_id})
            deleted_count += 1
            logger.info(f"Deleted duplicate price entry for medicine_id: {med_id}, platform: {platform}")
        else:
            seen_unique.add(unique_key)
            # If the casing is not lowercase, update it to lowercase
            if platform != platform_lower:
                prices_col.update_one({"_id": price_id}, {"$set": {"platform": platform_lower}})
                updated_count += 1
                logger.info(f"Updated platform casing for {price.get('medicine_name', 'Unknown')}: {platform} -> {platform_lower}")
                
    logger.info(f"Migration finished. Updated: {updated_count}, Deleted Duplicates: {deleted_count}")
    client.close()

if __name__ == "__main__":
    migrate()
