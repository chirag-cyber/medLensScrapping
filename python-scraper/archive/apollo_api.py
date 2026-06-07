import requests
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Origin': 'https://www.apollopharmacy.in',
    'Referer': 'https://www.apollopharmacy.in/'
}

# Testing apollo247 api
url = "https://search.apollo247.com/v2/search"
payload = {
    "searchString": "dolo 650",
    "page": 1,
    "size": 10,
    "storeId": "1"
}

print("Testing Apollo API:")
try:
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    print("Status:", r.status_code)
    print("Response:")
    print(r.text[:500])
except Exception as e:
    print(e)
