import requests
import json
import re

url = "https://www.apollopharmacy.in/search-medicines/dolo%20650"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
}

r = requests.get(url, headers=headers)
print("Status:", r.status_code)

# Try to extract the data payload from the Next.js stream
parts = re.findall(r'(\{"searchResults":.*?\}\]\})', r.text)
if parts:
    print("Found JSON substring matching searchResults!")
    print(parts[0][:500])
else:
    # Try finding product objects
    products = re.findall(r'\{"id":\d+,"name":"[^"]+","url":"[^"]+".*?"price":\d+.*?\}', r.text)
    if products:
         print(f"Found {len(products)} products with regex.")
         print(products[0])
    else:
         print("Regex found nothing. Dumping some Next.js text containing 'dolo':")
         for line in r.text.split('\n'):
             if 'dolo' in line.lower() and 'price' in line.lower():
                 print(line[:200])

