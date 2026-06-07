import os
import logging
import time
from pymongo import MongoClient
from dotenv import load_dotenv
from search_engine import UnifiedSearchEngine
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("price_sync.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

class PriceSynchronizer:
    """
    Synchronizes medicine prices in MongoDB with live pharmacy data.
    """
    
    def __init__(self, limit=10):
        self.client = MongoClient(MONGO_URL)
        self.db = self.client.get_database("MEDSAVE")
        self.medicines_col = self.db.get_collection("medicines")
        self.prices_col = self.db.get_collection("prices")
        self.engine = UnifiedSearchEngine(delay=1.5)
        self.limit = limit

    def run(self):
        """Iterate through medicines and update prices."""
        logger.info(f"Starting price synchronization for up to {self.limit} medicines...")
        
        # Get medicines that haven't been updated recently or are new
        # For demo, we just take the first N
        medicines = list(self.medicines_col.find({}).limit(self.limit))
        
        for med in medicines:
            med_id = med.get('_id')
            med_name = med.get('name')
            
            logger.info(f"Syncing prices for: {med_name} (ID: {med_id})")
            
            try:
                # Search across all platforms
                results = self.engine.search(med_name)
                
                if not results:
                    logger.warning(f"No results found for {med_name}")
                    continue
                
                # Update prices collection
                # We store all results for this medicine
                for res in results:
                    price_entry = {
                        "medicine_id": med_id,
                        "medicine_name": med_name,
                        "platform": res['platform'].lower(),
                        "name": res['name'],
                        "url": res['url'],
                        "mrp": res['mrp'],
                        "sale_price": res['sale_price'],
                        "pack_size": res['pack_size'],
                        "manufacturer": res['manufacturer'],
                        "in_stock": res['in_stock'],
                        "updated_at": datetime.utcnow()
                    }
                    
                    # Upsert based on medicine_id and platform
                    self.prices_col.update_one(
                        {"medicine_id": med_id, "platform": res['platform'].lower(), "url": res['url']},
                        {"$set": price_entry},
                        upsert=True
                    )
                
                logger.info(f"Successfully updated {len(results)} price sources for {med_name}")
                
            except Exception as e:
                logger.error(f"Error syncing {med_name}: {e}")
                
        logger.info("Price synchronization completed.")

    def close(self):
        self.engine.close()
        self.client.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync medicine prices from pharmacies.")
    parser.add_argument("--limit", type=int, default=5, help="Number of medicines to sync")
    args = parser.parse_args()
    
    sync = PriceSynchronizer(limit=args.limit)
    try:
        sync.run()
    finally:
        sync.close()
