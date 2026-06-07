import io
from scrapers.base import BaseScraper

scraper = BaseScraper()
html = scraper.get('https://www.1mg.com/drugs/878516')
if html:
    with io.open('calpol_html.txt', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Saved to calpol_html.txt")
else:
    print("Failed to get HTML")
