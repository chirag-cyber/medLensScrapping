import requests
from bs4 import BeautifulSoup

url = "https://www.apollopharmacy.in/search-medicines/dolo%20650"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36'
}
r = requests.get(url, headers=headers)
soup = BeautifulSoup(r.text, 'lxml')

print("Status:", r.status_code)
print("Scripts:")
for script in soup.find_all('script'):
    src = script.get('src')
    if src:
        print(f"  SRC: {src}")
    elif script.string:
        print(f"  INLINE ({len(script.string)} chars): {script.string[:80]}")

# Look for JSON embedded in NEXT_DATA or state
print("\n__NEXT_DATA__:", bool(soup.find('script', id='__NEXT_DATA__')))

# Look for price in HTML
price_tags = soup.find_all(string=lambda t: t and '₹' in t)
print(f"\nPrice tags found: {len(price_tags)}")
for p in price_tags[:5]:
    print(f"  {p.strip()}")
