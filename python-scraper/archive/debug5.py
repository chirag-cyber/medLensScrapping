import sys
import os
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')
client = MongoClient(os.getenv('MONGO_URL'))
prices = client['MEDSAVE']['prices']

med = client['MEDSAVE']['medicines'].find_one({'name': 'calpol 500 tablet'})
p = prices.find_one({'medicine_id': med['_id'], 'platform': 'netmeds'})
if p and p.get('url'):
    print(f"URL: {p.get('url')}")
    url = p.get('url').replace('/product/', '/prescriptions/')
    print(f"Prescription URL: {url}")
    html = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}).text
    soup = BeautifulSoup(html, 'lxml')
    for h2 in soup.find_all('h2'):
        if 'side effects' in h2.get_text(strip=True).lower():
            print('Found side effects on Netmeds!')
            sib = h2.find_next_sibling()
            if sib and sib.name == 'ul':
                for li in sib.find_all('li'):
                    print('-', li.get_text(strip=True))
            else:
                print('No UL sibling found.')
