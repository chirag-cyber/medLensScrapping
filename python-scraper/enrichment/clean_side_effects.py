import os
import re
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')
client = MongoClient(os.getenv('MONGO_URL'))
db = client['MEDSAVE']
medicines = db['medicines']

def clean_side_effects_array(effects):
    if not isinstance(effects, list):
        if isinstance(effects, str):
            # Split by commas or newlines if it's a string
            effects = re.split(r'[\n,]', effects)
        else:
            return []
            
    cleaned = []
    
    # Noise phrases to completely drop
    drop_phrases = [
        r'consult your doctor',
        r'no common side effects',
        r'precautions',
        r'warnings',
        r'directions for use',
        r'storage',
        r'antipyretics',
        r'most side effects do not require',
        r'disappear as your body adjusts'
    ]
    
    for item in effects:
        item = item.strip().strip('.').strip()
        
        # Skip empty
        if not item:
            continue
            
        # Check if item matches any drop phrase
        if any(re.search(phrase, item, re.IGNORECASE) for phrase in drop_phrases):
            continue
            
        # If it's too long (probably a paragraph instead of a bullet point)
        if len(item) > 40:
            continue
            
        # If it's just 1-2 characters
        if len(item) < 3:
            continue
            
        # Clean up weird characters and fix previous corruption
        item = item.replace("'", "").replace('’', "'")
        
        # Capitalize first letter
        item = item[0].upper() + item[1:]
        
        cleaned.append(item)
        
    # Remove duplicates while preserving order
    seen = set()
    result = []
    for item in cleaned:
        if item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
            
    return result

def run_cleanup():
    logger.info("Starting side_effects cleanup...")
    cursor = medicines.find({"side_effects": {"$exists": True, "$ne": None, "$ne": ""}})
    
    updated_count = 0
    cleared_count = 0
    total = 0
    
    for doc in cursor:
        total += 1
        original_se = doc.get('side_effects')
        
        cleaned_se = clean_side_effects_array(original_se)
        
        if original_se != cleaned_se:
            if len(cleaned_se) == 0:
                # Remove the field entirely if it's empty so the UI doesn't show an empty section
                medicines.update_one({'_id': doc['_id']}, {'$unset': {'side_effects': ""}})
                cleared_count += 1
                logger.info(f"Cleared side_effects for {doc.get('name')}")
            else:
                medicines.update_one({'_id': doc['_id']}, {'$set': {'side_effects': cleaned_se}})
                updated_count += 1
                logger.info(f"Updated side_effects for {doc.get('name')}: {cleaned_se}")
                
    logger.info(f"Cleanup complete. Total processed: {total}")
    logger.info(f"Updated {updated_count} medicines.")
    logger.info(f"Cleared {cleared_count} medicines.")

if __name__ == "__main__":
    run_cleanup()
