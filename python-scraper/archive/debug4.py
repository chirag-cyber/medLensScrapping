import requests
from bs4 import BeautifulSoup
html = requests.get('https://www.1mg.com/drugs/878516', headers={'User-Agent': 'Mozilla/5.0'}).text
soup = BeautifulSoup(html, 'lxml')
for el in soup.find_all(string=lambda t: t and 'side effect' in t.lower()):
    if el.parent.name in ['h2', 'h3', 'div', 'span']:
        print(f"Tag: {el.parent.name}, Class: {el.parent.get('class')}")
        print(f"Text: {el.strip()[:100]}")
        print("-" * 50)
