from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv('a:/medlens/medSave-India/.env')
db = MongoClient(os.getenv('MONGO_URL')).get_database('MEDSAVE')

meds = list(db.medicines.find({'name': {'$regex': 'ibugesic', '$options': 'i'}}))
med_ids = [m['_id'] for m in meds]

prices = db.prices.find({'medicine_id': {'$in': med_ids}})
deleted = 0

for p in prices:
    text = (p.get('name', '') + ' ' + p.get('url', '')).lower()
    # Check if any valid keyword is in the URL or Name
    if 'ibugesic' not in text and 'ibuprofen' not in text and 'brufen' not in text:
        print('Deleting:', p.get('platform'), text)
        db.prices.delete_one({'_id': p['_id']})
        deleted += 1

print('Deleted', deleted, 'bad ibugesic matches.')
