import os
import sys
import logging
from pymongo import MongoClient
from dotenv import load_dotenv
from detail_scraper import ClinicalDetailScraper

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')
client = MongoClient(os.getenv('MONGO_URL'))
db = client['MEDSAVE']
medicines = db['medicines']
prices = db['prices']

query = {
    '$or': [
        {'side_effects': {'$exists': False}},
        {'side_effects': None},
        {'side_effects': []},
        {'side_effects': ''}
    ]
}

# Get a batch of medicines
meds = list(medicines.find(query).limit(50))
scraper = ClinicalDetailScraper()

found = 0
for med in meds:
    med_id = med['_id']
    onemg_url = None
    p_1mg = prices.find_one({'medicine_id': med_id, 'platform': '1MG'})
    if p_1mg: onemg_url = p_1mg.get('url')
    
    if onemg_url:
        print(f"Testing medicine: {med.get('name')}")
        print(f"1mg URL: {onemg_url}")
        
        res = scraper.fetch_clinical_data(onemg_url=onemg_url, netmeds_url=None)
        print("Scraped Side effects:")
        print(res.get('side_effects'))
        print("-" * 50)
        
        found += 1
        if found >= 3:
            break

scraper.close()
