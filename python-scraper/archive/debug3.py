import os
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient
from dotenv import load_dotenv

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

onemg_urls = {}
for pdoc in prices.find({'url': {'$exists': True, '$ne': ''}}, {'medicine_id': 1, 'platform': 1, 'url': 1}):
    if pdoc.get('platform', '').lower() == '1mg':
        onemg_urls[str(pdoc.get('medicine_id'))] = pdoc['url']

meds = list(medicines.find(query).limit(50))
found = 0
for med in meds:
    url = onemg_urls.get(str(med['_id']))
    if url:
        print(f"Medicine: {med.get('name')}")
        print(f"URL: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        html = requests.get(url, headers=headers).text
        soup = BeautifulSoup(html, 'lxml')
        
        has_se = False
        for h2 in soup.find_all('h2'):
            if 'side effects' in h2.get_text(strip=True).lower():
                print('Found side effects heading!')
                sib = h2.find_next_sibling()
                if sib: 
                    print("Sibling text:")
                    print(sib.get_text(separator=' | ', strip=True))
                has_se = True
                break
        
        if not has_se:
            print("No 'side effects' heading found in HTML!")
            
        print("-" * 50)
        found += 1
        if found >= 3:
            break
