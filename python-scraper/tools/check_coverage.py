"""Check how many unique salts exist, and how many will be covered after 1mg enrichment completes."""
from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv(os.path.join('..', '.env'))
c = MongoClient(os.getenv('MONGO_URL'))
m = c['MEDSAVE']['medicines']
p = c['MEDSAVE']['prices']

total = m.count_documents({})
has_uses = m.count_documents({'uses': {'$exists': True}})

# Count unique salts across ALL medicines
all_salts = set()
no_salt = 0
for doc in m.find({}, {'salt': 1}):
    salt = (doc.get('salt') or '').strip().lower()
    if salt and len(salt) > 2:
        all_salts.add(salt)
    else:
        no_salt += 1

print(f"Total medicines: {total}")
print(f"Currently enriched (has uses): {has_uses}")
print(f"Unique salts in DB: {len(all_salts)}")
print(f"Medicines without salt: {no_salt}")

# Now check: how many unique salts have a 1mg URL?
# These will definitely get enriched when 1mg script finishes
onemg_med_ids = set()
for price_doc in p.find({"platform": "1mg", "url": {"$exists": True, "$ne": ""}}, {"medicine_id": 1}):
    mid = price_doc.get("medicine_id")
    if mid:
        onemg_med_ids.add(str(mid))

salts_with_1mg = set()
salts_without_1mg = set()
meds_without_1mg_but_have_salt = 0

for doc in m.find({}, {'_id': 1, 'salt': 1}):
    salt = (doc.get('salt') or '').strip().lower()
    if not salt or len(salt) <= 2:
        continue
    mid = str(doc['_id'])
    if mid in onemg_med_ids:
        salts_with_1mg.add(salt)
    else:
        salts_without_1mg.add(salt)
        meds_without_1mg_but_have_salt += 1

# Salts that are ONLY on non-1mg medicines (truly uncovered)
truly_uncovered_salts = salts_without_1mg - salts_with_1mg
covered_by_sibling = salts_without_1mg & salts_with_1mg

print(f"\n--- After 1mg finishes + siblings run ---")
print(f"Salts covered by 1mg directly: {len(salts_with_1mg)}")
print(f"Medicines without 1mg URL but SAME salt exists in 1mg: {len(covered_by_sibling)} salts")
print(f"  -> These will be filled by sibling enrichment!")
print(f"Truly uncovered salts (no 1mg medicine has this salt): {len(truly_uncovered_salts)}")

# Count how many medicines have truly uncovered salts
truly_uncovered_count = 0
for doc in m.find({}, {'_id': 1, 'salt': 1}):
    salt = (doc.get('salt') or '').strip().lower()
    if salt in truly_uncovered_salts:
        truly_uncovered_count += 1

print(f"Medicines with truly uncovered salts: {truly_uncovered_count}")
print(f"\n--- PROJECTED FINAL COVERAGE ---")
projected_covered = total - truly_uncovered_count - no_salt
print(f"Will be enriched: {projected_covered}/{total} ({round(projected_covered/total*100,1)}%)")
print(f"Gaps: {truly_uncovered_count + no_salt} medicines")

# Show some example uncovered salts
print(f"\n--- Sample uncovered salts (first 15) ---")
for s in sorted(truly_uncovered_salts)[:15]:
    count = m.count_documents({'salt': {'$regex': f'^{s[:20]}', '$options': 'i'}})
    print(f"  {s[:80]} ({count} medicines)")
