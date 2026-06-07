import sys
import json
import logging
from scrapers.apollo import ApolloScraper

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    scraper = ApolloScraper()
    
    print(f"Testing Apollo Scraper for: '{query}'")
    results = scraper.search_medicine(query)
    print(f"Found {len(results)} results.")
    for r in results[:3]:
        print(json.dumps(r, indent=2))
