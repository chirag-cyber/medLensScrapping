from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv('a:/medlens/medSave-India/.env')
db = MongoClient(os.getenv('MONGO_URL')).get_database('MEDSAVE')

cursor = db.medicines.find({'name': {'$regex': 'ibugesic', '$options': 'i'}})
for med in cursor:
    print('Medicine:', med.get('name'), str(med['_id']))
    prices = db.prices.find({'medicine_id': med['_id']})
    print('Prices:')
    for p in prices:
        print(' - ', p['platform'], p.get('name', p.get('medicine_name', 'UNKNOWN_NAME')), 'URL:', p.get('url', ''))
    print('---')
