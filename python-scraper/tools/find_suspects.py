from pymongo import MongoClient
import os
import re
from dotenv import load_dotenv

load_dotenv('a:/medlens/medSave-India/.env')
db = MongoClient(os.getenv('MONGO_URL')).get_database('MEDSAVE')

print("Fetching medicines...")
meds = list(db.medicines.find({}, {"_id": 1, "name": 1}))
med_dict = {m['_id']: m['name'] for m in meds}

print("Fetching prices...")
prices = db.prices.find({})
suspects = []

for p in prices:
    med_id = p.get('medicine_id')
    if not med_id or med_id not in med_dict:
        continue
        
    actual_med_name = med_dict[med_id].lower()
    price_name = p.get('name', '').lower()
    url = p.get('url', '').lower()
    
    text_to_check = price_name if price_name else url
    if not text_to_check:
        continue
        
    words = actual_med_name.split()
    first_word = None
    for w in words:
        if not re.match(r'^\d+\.?\d*(mg|ml|gm|mcg|g)?$', w, re.IGNORECASE):
            first_word = w
            break
            
    if first_word and first_word not in text_to_check:
        suspects.append({
            'medicine_name': med_dict[med_id],
            'platform': p.get('platform', 'unknown'),
            'price_name': p.get('name', 'N/A'),
            'url': p.get('url', 'N/A')
        })

print(f"Found {len(suspects)} suspect matches. Writing to artifact...")

output_path = r"C:\Users\Chira\.gemini\antigravity-ide\brain\de2c6a8e-4fa9-461c-8f4b-88693250a0c8\suspect_matches.md"

with open(output_path, "w", encoding="utf-8") as f:
    f.write("# Suspected Incorrect Medicine Matches\n\n")
    f.write("The following prices are linked to a medicine, but their name/URL does not contain the primary brand name.\n\n")
    
    # Group by medicine name for readability
    grouped = {}
    for s in suspects:
        grouped.setdefault(s['medicine_name'], []).append(s)
        
    for med, matches in grouped.items():
        f.write(f"### {med}\n")
        f.write("| Platform | Matched Name | URL |\n")
        f.write("|----------|--------------|-----|\n")
        for m in matches:
            name = m['price_name'].replace('|', '\\|')
            url = m['url']
            url_display = f"[Link]({url})" if url.startswith('http') else url
            f.write(f"| {m['platform']} | {name} | {url_display} |\n")
        f.write("\n")

print(f"Report written to {output_path}")
