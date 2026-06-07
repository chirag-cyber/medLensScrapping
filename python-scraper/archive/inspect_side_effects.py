import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')
client = MongoClient(os.getenv('MONGO_URL'))
db = client['MEDSAVE']

meds = list(db['medicines'].find({'side_effects': {'$exists': True, '$ne': ''}}).limit(10))
for m in meds:
    print(f"Name: {m.get('name')}")
    print(f"Type: {type(m.get('side_effects'))}")
    print(f"Side Effects: {m.get('side_effects')}")
    print('-'*40)
