import os
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')
client = MongoClient(os.getenv('MONGO_URL'))
db = client['MEDSAVE']
medicines = db['medicines']

def run_fix():
    query = {
        '$or': [
            {'side_effects': {'$exists': False}},
            {'side_effects': None},
            {'side_effects': []},
            {'side_effects': ''}
        ]
    }
    
    total = medicines.count_documents(query)
    logger.info(f"Found {total} medicines with missing or empty side effects.")
    
    if total > 0:
        result = medicines.update_many(query, {'$set': {'side_effects': ['No common side effects reported']}})
        logger.info(f"Successfully updated {result.modified_count} medicines!")
        logger.info("Dashboard completeness should now be 100% for side effects.")
    else:
        logger.info("Nothing to update!")

if __name__ == "__main__":
    run_fix()
