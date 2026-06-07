"""
inspect_malformed.py — Find and categorize malformed clinical data in medicines collection.
"""
import os
import re
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
client = MongoClient(os.getenv('MONGO_URL'))
db = client['MEDSAVE']
meds = db['medicines']

print("=" * 60)
print("MALFORMED CLINICAL DATA AUDIT")
print("=" * 60)

# ── 1. Check description quality ──
print("\n--- DESCRIPTION QUALITY ---")
descs = list(meds.find({'description': {'$exists': True, '$ne': ''}}, {'name': 1, 'description': 1}).limit(5000))

junk_patterns = {
    'page_title_noise': re.compile(r'(buy\s+online|order\s+now|best\s+price|free\s+delivery|flat\s+\d+%\s+off|cashback|use\s+code|limited\s+offer)', re.I),
    'html_tags': re.compile(r'<[^>]+>'),
    'markdown_artifacts': re.compile(r'\[!\[|\]\(http|#{3,}'),
    'too_short': None,  # handled separately
    'promotional': re.compile(r'(MedPlus|PharmEasy|1mg|Apollo|Netmeds|Amazon)\s+(is|offers|provides)', re.I),
    'encoding_garbage': re.compile(r'[^\x00-\x7F\u0900-\u097F\u20B9]{3,}'),  # non-ASCII non-Hindi clusters
}

issues = {k: [] for k in junk_patterns}
issues['too_short'] = []

for doc in descs:
    desc = doc.get('description', '')
    name = doc.get('name', '')
    
    if not desc or len(desc) < 20:
        issues['too_short'].append((name, desc))
        continue
    
    for pattern_name, pattern in junk_patterns.items():
        if pattern and pattern.search(desc):
            issues[pattern_name].append((name, desc[:80]))

for issue, examples in issues.items():
    count = len(examples)
    if count > 0:
        print(f"\n  {issue}: {count} medicines")
        for name, snippet in examples[:3]:
            print(f"    - {name}: \"{snippet}...\"")

# ── 2. Check side_effects quality ──
print("\n--- SIDE EFFECTS QUALITY ---")
se_docs = list(meds.find({'side_effects': {'$exists': True}}, {'name': 1, 'side_effects': 1}).limit(5000))

se_issues = {'empty_list': 0, 'string_not_list': 0, 'junk_entries': 0, 'too_many': 0}
se_examples = {}

for doc in se_docs:
    se = doc.get('side_effects', [])
    name = doc.get('name', '')
    
    if isinstance(se, list) and len(se) == 0:
        se_issues['empty_list'] += 1
    elif isinstance(se, str):
        se_issues['string_not_list'] += 1
        se_examples.setdefault('string_not_list', []).append((name, se[:80]))
    elif isinstance(se, list):
        if len(se) > 20:
            se_issues['too_many'] += 1
            se_examples.setdefault('too_many', []).append((name, f"{len(se)} items"))
        # Check for junk entries
        for item in se:
            if isinstance(item, str) and (len(item) < 2 or len(item) > 100):
                se_issues['junk_entries'] += 1
                se_examples.setdefault('junk_entries', []).append((name, item[:80]))
                break

for issue, count in se_issues.items():
    if count > 0:
        print(f"  {issue}: {count}")
        for name, snippet in se_examples.get(issue, [])[:3]:
            print(f"    - {name}: \"{snippet}\"")

# ── 3. Check uses quality ──
print("\n--- USES QUALITY ---")
uses_docs = list(meds.find({'uses': {'$exists': True, '$ne': ''}}, {'name': 1, 'uses': 1}).limit(5000))

uses_issues = {'string_not_list': 0, 'html_in_uses': 0, 'show_more_artifacts': 0, 'very_long_single': 0}
uses_examples = {}

for doc in uses_docs:
    uses = doc.get('uses', '')
    name = doc.get('name', '')
    
    if isinstance(uses, str):
        uses_issues['string_not_list'] += 1
        uses_examples.setdefault('string_not_list', []).append((name, uses[:80]))
        if '<' in uses:
            uses_issues['html_in_uses'] += 1
        if 'show more' in uses.lower() or 'show less' in uses.lower():
            uses_issues['show_more_artifacts'] += 1
            uses_examples.setdefault('show_more_artifacts', []).append((name, uses[:80]))
    elif isinstance(uses, list):
        for item in uses:
            if isinstance(item, str):
                if 'show more' in item.lower() or 'show less' in item.lower():
                    uses_issues['show_more_artifacts'] += 1
                    uses_examples.setdefault('show_more_artifacts', []).append((name, item[:80]))
                if len(item) > 300:
                    uses_issues['very_long_single'] += 1
                    uses_examples.setdefault('very_long_single', []).append((name, item[:80]))

for issue, count in uses_issues.items():
    if count > 0:
        print(f"  {issue}: {count}")
        for name, snippet in uses_examples.get(issue, [])[:3]:
            print(f"    - {name}: \"{snippet}\"")

# ── 4. Check safety_advice quality ──
print("\n--- SAFETY ADVICE QUALITY ---")
sa_docs = list(meds.find({'safety_advice': {'$exists': True}}, {'name': 1, 'safety_advice': 1}).limit(5000))

sa_issues = {'string_not_dict': 0, 'empty_dict': 0, 'missing_keys': 0}

for doc in sa_docs:
    sa = doc.get('safety_advice', {})
    name = doc.get('name', '')
    
    if isinstance(sa, str):
        sa_issues['string_not_dict'] += 1
    elif isinstance(sa, dict):
        if len(sa) == 0:
            sa_issues['empty_dict'] += 1
        elif len(sa) < 3:
            sa_issues['missing_keys'] += 1

for issue, count in sa_issues.items():
    if count > 0:
        print(f"  {issue}: {count}")

# ── 5. Check FAQ quality ──
print("\n--- FAQ QUALITY ---")
faq_docs = list(meds.find({'faq': {'$exists': True}}, {'name': 1, 'faq': 1}).limit(5000))

faq_issues = {'empty_list': 0, 'string_not_list': 0, 'bad_format': 0, 'good': 0}

for doc in faq_docs:
    faq = doc.get('faq', [])
    if isinstance(faq, list):
        if len(faq) == 0:
            faq_issues['empty_list'] += 1
        elif faq and isinstance(faq[0], dict) and 'question' in faq[0]:
            faq_issues['good'] += 1
        else:
            faq_issues['bad_format'] += 1
    else:
        faq_issues['string_not_list'] += 1

for issue, count in faq_issues.items():
    print(f"  {issue}: {count}")

# ── 6. Sample of existing clinical data to see quality ──
print("\n--- SAMPLE: EXISTING CLINICAL DATA (first with uses) ---")
sample = meds.find_one({'uses': {'$exists': True, '$ne': '', '$ne': []}})
if sample:
    print(f"  Name: {sample.get('name','')}")
    print(f"  Uses type: {type(sample.get('uses',''))}")
    print(f"  Uses: {str(sample.get('uses',''))[:200]}")
    print(f"  Safety type: {type(sample.get('safety_advice',''))}")
    print(f"  Safety: {str(sample.get('safety_advice',''))[:200]}")
    print(f"  How to use: {str(sample.get('how_to_use',''))[:100]}")
    print(f"  Source: {sample.get('clinical_data_source','')}")

client.close()
