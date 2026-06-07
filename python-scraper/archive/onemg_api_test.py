import requests
import json

url = "https://www.1mg.com/pwa-api/api/v4/search/all"
params = {
    "name": "dolo 650",
    "city": "New Delhi"
}
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://www.1mg.com',
    'Referer': 'https://www.1mg.com/search/all?name=dolo%20650'
}

r = requests.get(url, params=params, headers=headers)
print("Status:", r.status_code)
if r.status_code == 200:
    data = r.json()
    skus = data.get('data', {}).get('skus', [])
    print(f"SKUs found: {len(skus)}")
    if skus:
        print(json.dumps(skus[0], indent=2))
else:
    print(r.text[:500])
