"""Deep-dive into platforms with embedded JSON data."""
import requests, json
from bs4 import BeautifulSoup

H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0',
    'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate',
    'Upgrade-Insecure-Requests': '1',
}

# === PharmEasy: __NEXT_DATA__ has searchResults ===
print("=== PHARMEASY SEARCH ===")
r = requests.get('https://pharmeasy.in/search/all?name=dolo+650', headers=H, timeout=15)
soup = BeautifulSoup(r.text, 'lxml')
nd = soup.find('script', id='__NEXT_DATA__')
if nd:
    data = json.loads(nd.string)
    results = data.get('props', {}).get('pageProps', {}).get('searchResults', [])
    print(f"Search results: {len(results)}")
    for item in results[:3]:
        print(f"  Name: {item.get('name', '?')}")
        print(f"  Slug: {item.get('slug', '?')}")
        print(f"  MRP: {item.get('mrp', '?')}")
        print(f"  Price: {item.get('salePrice', item.get('price', '?'))}")
        print(f"  Manufacturer: {item.get('manufacturer', '?')}")
        print(f"  Pack: {item.get('packSize', '?')}")
        print(f"  Keys: {list(item.keys())[:10]}")
        print()

# === Apollo: Check if product page has price in HTML ===
print("\n=== APOLLO PRODUCT ===")
r2 = requests.get('https://www.apollopharmacy.in/otc/dolo-650mg-tablet-15-s', headers=H, timeout=15)
soup2 = BeautifulSoup(r2.text, 'lxml')
# Check for price patterns
import re
prices_found = re.findall(r'₹\s*[\d,.]+', r2.text[:50000])
print(f"Prices found in HTML: {prices_found[:5]}")
# Check JSON-LD
jlds = soup2.find_all('script', type='application/ld+json')
for jld in jlds:
    try:
        d = json.loads(jld.string)
        if isinstance(d, dict) and d.get('@type') in ['Product', 'Drug']:
            print(f"  JSON-LD Product: {d.get('name', '?')}")
            offers = d.get('offers', {})
            print(f"  Price: {offers.get('price', '?')}")
    except:
        pass

# Check __INITIAL_STATE__ or similar
for script in soup2.find_all('script'):
    if script.string and 'window.__' in (script.string or '')[:50]:
        key = script.string[:100]
        print(f"  Window var: {key}")

# === Amazon: Check search results ===
print("\n=== AMAZON SEARCH ===")
r3 = requests.get('https://www.amazon.in/s?k=dolo+650+tablet', headers=H, timeout=15)
soup3 = BeautifulSoup(r3.text, 'lxml')
# Amazon products are in data-component-type="s-search-result"
results3 = soup3.find_all('div', attrs={'data-component-type': 's-search-result'})
print(f"Search results: {len(results3)}")
for r in results3[:2]:
    name = r.find('h2')
    price = r.find('span', class_='a-price-whole')
    print(f"  Name: {name.get_text(strip=True)[:60] if name else '?'}")
    print(f"  Price: {price.get_text(strip=True) if price else '?'}")
