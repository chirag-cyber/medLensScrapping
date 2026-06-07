from pymongo import MongoClient
import os
import re
from dotenv import load_dotenv

load_dotenv('a:/medlens/medSave-India/.env')
db = MongoClient(os.getenv('MONGO_URL')).get_database('MEDSAVE')

# Cache medicines for fast lookup
meds = list(db.medicines.find({}, {"_id": 1, "name": 1}))
med_dict = {m['_id']: m['name'] for m in meds}

prices = db.prices.find({})
deleted_count = 0

for p in prices:
    med_id = p.get('medicine_id')
    if not med_id or med_id not in med_dict:
        continue
        
    actual_med_name = med_dict[med_id].lower()
    price_name = p.get('name', '').lower()
    url = p.get('url', '').lower()
    
    # Text to check against (prefer name, fallback to url)
    text_to_check = price_name if price_name else url
    
    if not text_to_check:
        continue
        
    # Extract first word of the actual medicine name (the brand)
    words = actual_med_name.split()
    first_word = None
    for w in words:
        if not re.match(r'^\d+\.?\d*(mg|ml|gm|mcg|g)?$', w, re.IGNORECASE):
            first_word = w
            break
            
    if first_word and first_word not in text_to_check:
        print(f"Deleting bad match: actual='{actual_med_name}' matched to '{price_name}' / URL '{url}' on {p.get('platform')}")
        db.prices.delete_one({'_id': p['_id']})
        deleted_count += 1

print(f"\nDeleted {deleted_count} bad matches from the database.")
