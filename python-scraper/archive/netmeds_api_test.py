import requests
import json
from bs4 import BeautifulSoup

url = "https://www.netmeds.com/catalogsearch/result/dolo%20650/all"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36'
}

print("Testing Netmeds Search...")
r = requests.get(url, headers=headers)
print("Status:", r.status_code)

# Netmeds search has an API:
api_url = "https://napi.netmeds.com/global-search/v1/search"
payload = {"q": "dolo 650", "page": 1, "per_page": 10, "type": "product"}
r2 = requests.post(api_url, json=payload, headers=headers)
print("API Status:", r2.status_code)
if r2.status_code == 200:
    data = r2.json()
    products = data.get('data', {}).get('products', [])
    print(f"Products found: {len(products)}")
    for p in products[:2]:
        print(p.get('display_name'), p.get('mrp'), p.get('price'))
