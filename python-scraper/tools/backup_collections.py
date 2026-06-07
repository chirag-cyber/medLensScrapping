"""
backup_collections.py — Create timestamped backups of medicines and prices collections.
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    print("ERROR: No MONGO_URL found in .env")
    sys.exit(1)

client = MongoClient(MONGO_URL)
db = client['MEDSAVE']

timestamp = datetime.now().strftime('%Y%m%d_%H%M')

collections_to_backup = ['medicines', 'prices']

for col_name in collections_to_backup:
    backup_name = f"{col_name}_backup_{timestamp}"
    source = db[col_name]
    count = source.count_documents({})
    
    print(f"Backing up '{col_name}' ({count} docs) -> '{backup_name}'...")
    
    # Use aggregation $out for server-side copy (fast, no network transfer)
    pipeline = [{"$match": {}}, {"$out": backup_name}]
    source.aggregate(pipeline)
    
    # Verify
    backup_count = db[backup_name].count_documents({})
    if backup_count == count:
        print(f"  OK: {backup_count} docs backed up successfully.")
    else:
        print(f"  WARNING: Source had {count} docs but backup has {backup_count}!")

# List all backup collections
print(f"\n--- All collections in MEDSAVE ---")
for name in sorted(db.list_collection_names()):
    if 'backup' in name:
        count = db[name].count_documents({})
        print(f"  [BACKUP] {name}: {count} docs")
    else:
        count = db[name].count_documents({})
        print(f"  {name}: {count} docs")

client.close()
print("\nBackup complete.")
