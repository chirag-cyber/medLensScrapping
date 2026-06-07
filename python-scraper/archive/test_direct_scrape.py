"""Verify the NEWLY enriched side effects from our BS4 scraper."""
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(os.path.join('..', '.env'))
c = MongoClient(os.getenv('MONGO_URL'))
m = c['MEDSAVE']['medicines']

# Check the ones we just force-updated
for name in ['-sr2', '1 al 10mg', '1 al m']:
    doc = m.find_one({'normalized_name': name})
    if doc:
        n = doc.get('name', '?')
        se = doc.get('side_effects', '')
        print(f"=== {n} ===")
        print(f"Side effects type: {type(se).__name__}")
        if isinstance(se, str):
            print(f"Content ({len(se)} chars):")
            print(se[:500])
        elif isinstance(se, list):
            print(f"Content ({len(se)} items):")
            print(se[:5])
        print()

c.close()
