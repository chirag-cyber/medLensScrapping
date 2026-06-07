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

med = medicines.find_one(query)
if med:
    print(f"Testing medicine: {med.get('name')}")
    med_id = med['_id']
    
    # In MongoDB, medicine_id might be stored as an ObjectId or String. The prices coll usually stores as ObjectId.
    onemg_url = None
    p_1mg = prices.find_one({'medicine_id': med_id, 'platform': '1MG'})
    if p_1mg: onemg_url = p_1mg.get('url')
    
    netmeds_url = None
    p_nm = prices.find_one({'medicine_id': med_id, 'platform': 'NETMEDS'})
    if p_nm: netmeds_url = p_nm.get('url')
    
    print(f"1mg URL: {onemg_url}")
    print(f"Netmeds URL: {netmeds_url}")
    
    scraper = ClinicalDetailScraper()
    res = scraper.fetch_clinical_data(onemg_url=onemg_url, netmeds_url=netmeds_url)
    print("Scraped Clinical Data:")
    print(res.get('side_effects'))
    scraper.close()
else:
    print("No medicines found matching query.")
