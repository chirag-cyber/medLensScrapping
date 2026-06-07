import os
from dotenv import load_dotenv
from pymongo import MongoClient
import sys

# Load .env from parent dir
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
MONGO_URL = os.getenv("MONGO_URL")

if not MONGO_URL:
    print("MONGO_URL not found!")
    sys.exit(1)

client = MongoClient(MONGO_URL)
db = client.get_database("MEDSAVE")
prices_col = db.get_collection("prices")

count = prices_col.count_documents({"platform": "platinumrx"})
print(f"Total platinumrx prices in DB: {count}")

sample = prices_col.find_one({"platform": "platinumrx"})
print(f"Sample: {sample}")

platforms = prices_col.distinct("platform")
print(f"Distinct platforms: {platforms}")
