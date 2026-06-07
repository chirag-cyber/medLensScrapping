from bs4 import BeautifulSoup
import json

with open("1mg_test.html", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, 'lxml')

# Look for product cards, 1mg uses div class style-__pro-title__ or style__product-card__
cards = soup.find_all('div', class_=lambda c: c and 'product-card' in c.lower())
if not cards:
    cards = soup.find_all('a', href=lambda h: h and '/drugs/' in h)

print(f"Found {len(cards)} potential product cards/links.")
for card in cards[:3]:
    print("---")
    print(card.get_text(strip=True)[:100].replace('\u20b9', 'Rs.'))
    if card.name == 'a':
        print("URL:", card.get('href'))
