import os
import sys
import time
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add parent directory to path so we can import from scrapers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detail_scraper import ClinicalDetailScraper

# Load environment
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
MONGO_URL = os.getenv('MONGO_URL')

def fill_missing_side_effects(limit=0):
    if not MONGO_URL:
        logger.error("No MONGO_URL found in .env")
        return

    client = MongoClient(MONGO_URL)
    db = client['MEDSAVE']
    medicines = db['medicines']
    prices = db['prices']

    # Find medicines missing side effects (either field doesn't exist, is null, or is empty array/string)
    query = {
        '$or': [
            {'side_effects': {'$exists': False}},
            {'side_effects': None},
            {'side_effects': []},
            {'side_effects': ''}
        ]
    }
    
    total_missing = medicines.count_documents(query)
    logger.info(f"Found {total_missing} medicines missing side effects.")
    
    if total_missing == 0:
        logger.info("Nothing to do!")
        return

    effective_limit = limit if limit > 0 else total_missing
    logger.info(f"Processing up to {effective_limit} medicines...")

    # Build URL index for quick lookup
    logger.info("Building URL index from prices collection...")
    onemg_urls = {}
    netmeds_urls = {}
    for pdoc in prices.find({'url': {'$exists': True, '$ne': ''}}, {'medicine_id': 1, 'platform': 1, 'url': 1}):
        med_id = str(pdoc.get('medicine_id'))
        if pdoc['platform'].lower() == '1mg':
            onemg_urls[med_id] = pdoc['url']
        elif pdoc['platform'].lower() == 'netmeds':
            netmeds_urls[med_id] = pdoc['url']
    
    logger.info(f"Indexed {len(onemg_urls)} 1mg URLs and {len(netmeds_urls)} Netmeds URLs.")

    scraper = ClinicalDetailScraper(delay=0.5)
    cursor = medicines.find(query).limit(effective_limit)
    
    processed = 0
    updated = 0
    no_url = 0
    no_data = 0

    for doc in cursor:
        processed += 1
        med_id = str(doc['_id'])
        name = doc.get('name', 'Unknown')
        
        url_1mg = onemg_urls.get(med_id)
        url_netmeds = netmeds_urls.get(med_id)
        
        if not url_1mg and not url_netmeds:
            no_url += 1
            if processed % 50 == 0:
                logger.info(f"[{processed}/{effective_limit}] {name} - No URL found in prices")
            continue
            
        try:
            # Fetch data using the standard scraping system
            clinical = scraper.fetch_clinical_data(onemg_url=url_1mg, netmeds_url=url_netmeds)
            
            new_side_effects = clinical.get('side_effects', [])
            
            if new_side_effects and len(new_side_effects) > 0:
                # Clean up the scraped side effects directly
                cleaned = []
                drop_phrases = ['consult your doctor', 'no common side effects', 'precautions', 'warnings', 'directions for use', 'storage', 'antipyretics', 'most side effects do not require', 'disappear as your body adjusts']
                
                for item in new_side_effects:
                    item = item.strip().strip('.').strip()
                    if not item or len(item) < 3 or len(item) > 60: continue
                    if any(phrase in item.lower() for phrase in drop_phrases): continue
                    item = item.replace("'", "").replace('’', "'")
                    item = item[0].upper() + item[1:]
                    cleaned.append(item)
                
                # Remove duplicates
                cleaned = list(dict.fromkeys(cleaned))
                
                if cleaned:
                    medicines.update_one({'_id': doc['_id']}, {'$set': {'side_effects': cleaned}})
                    updated += 1
                    logger.info(f"[{processed}/{effective_limit}] {name} - Updated {len(cleaned)} side effects")
                else:
                    no_data += 1
            else:
                no_data += 1
                
        except Exception as e:
            logger.error(f"[{processed}/{effective_limit}] {name} - Error: {e}")
            
        time.sleep(0.5)
        
        if processed % 100 == 0:
            logger.info(f"--- CHECKPOINT: Processed: {processed}, Updated: {updated}, No URL: {no_url}, No Data: {no_data} ---")

    logger.info("="*50)
    logger.info(f"COMPLETED. Processed: {processed}, Updated: {updated}, No URL: {no_url}, No Data: {no_data}")
    
    scraper.close()
    client.close()

if __name__ == "__main__":
    fill_missing_side_effects()
