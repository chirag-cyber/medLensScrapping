"""
llm_side_effects_fix.py — Backfill placeholder side_effects via a Groq LLM.

Targets only medicines whose side_effects is the placeholder
['No common side effects reported']. Side effects are a property of the active
ingredient (salt), NOT the brand, so we deduplicate by normalized salt and make
ONE LLM call per unique composition, then fan the result to every brand that
shares it. On a catalog with many brands per salt this cuts LLM calls (and token
spend) 5-10x versus the old one-call-per-medicine loop.

Model: openai/gpt-oss-20b. The previous llama-3.1-8b-instant /
llama-3.3-70b-versatile Groq models were deprecated 2026-06-17 (shutdown
2026-08-16), so they are not used here. Output is capped with max_tokens and
calls retry with exponential backoff on rate limits.
"""

import os
import sys
import json
import time
import logging

from pymongo import MongoClient
from dotenv import load_dotenv
from groq import Groq

try:
    from groq import RateLimitError
except Exception:  # SDK layout differences — fall back to string sniffing.
    RateLimitError = None

# Reuse the identity pipeline's salt normalizer + molecular guard so groups line
# up with the collection's normalized_salt convention (parent dir on path).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from medicine_identity import _normalize_salt_key, _salt_is_molecular

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

MONGO_URL = os.getenv('MONGO_URL')
# Support both GROQ_API_KEY and the root .env's plural GROQ_API_KEYS (comma list).
GROQ_API_KEY = os.getenv('GROQ_API_KEY') or (
    os.getenv('GROQ_API_KEYS', '').split(',')[0].strip() or None)

if not MONGO_URL or not GROQ_API_KEY:
    logger.error("Missing MONGO_URL or GROQ_API_KEY/GROQ_API_KEYS")
    sys.exit(1)

MODEL = "openai/gpt-oss-20b"
MAX_TOKENS = 150          # a side-effects list is short; cap runaway output
MAX_RETRIES = 4
BACKOFF_BASE = 2.0        # seconds; doubled per retry on rate limit
CALL_DELAY = 1.0          # polite spacing between distinct LLM calls

client = MongoClient(MONGO_URL)
db = client['MEDSAVE']
medicines = db['medicines']
groq_client = Groq(api_key=GROQ_API_KEY)


def _llm_side_effects(salt: str, name: str):
    """Ask the model for common side effects. Returns a cleaned list or None.
    Retries with exponential backoff on rate limits / transient errors."""
    subject = f"salt/composition '{salt}'" if salt else f"medicine '{name}'"
    prompt = (
        f"List the most common side effects for a medicine with {subject}.\n"
        'Return a JSON object with a single key "side_effects" whose value is an '
        'array of short strings (1-3 words each), e.g. '
        '{"side_effects": ["Nausea", "Headache", "Dizziness"]}.\n'
        "No markdown, no extra text. JUST the JSON object. "
        'If you do not know, return {"side_effects": ["Unknown"]}.'
    )
    for attempt in range(MAX_RETRIES):
        try:
            completion = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You output JSON only."},
                    {"role": "user", "content": prompt},
                ],
                model=MODEL,
                temperature=0.1,
                max_tokens=MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(completion.choices[0].message.content.strip())
            se = parsed.get("side_effects", [])
            if isinstance(se, list) and se and se[0] != "Unknown":
                return [str(s).strip().capitalize() for s in se
                        if isinstance(s, str) and str(s).strip()]
            return None
        except Exception as e:
            is_rate = (RateLimitError and isinstance(e, RateLimitError)) \
                or "rate" in str(e).lower() or "429" in str(e)
            wait = BACKOFF_BASE * (2 ** attempt) if is_rate else BACKOFF_BASE
            logger.warning("LLM call failed (attempt %d/%d)%s: %s — retry in %.1fs",
                           attempt + 1, MAX_RETRIES,
                           " [rate-limit]" if is_rate else "", e, wait)
            time.sleep(wait)
    logger.error("LLM gave up for %s after %d attempts", subject, MAX_RETRIES)
    return None


def fix_side_effects():
    query = {'side_effects': ['No common side effects reported']}
    docs = list(medicines.find(query, {'_id': 1, 'name': 1, 'salt': 1}))
    total = len(docs)
    logger.info("Found %d medicines with placeholder side_effects", total)
    if total == 0:
        return

    # Group by molecular salt key so one call serves every brand sharing it.
    # Empty or non-molecular (prose) salt can't be trusted to group — those get
    # their own per-document group and a name-based prompt.
    groups = {}
    for d in docs:
        salt = d.get('salt', '') or ''
        key = _normalize_salt_key(salt)
        if key and _salt_is_molecular(key):
            g = groups.setdefault(('salt', key),
                                   {"salt": salt, "name": d.get('name', ''), "ids": []})
        else:
            g = groups.setdefault(('doc', str(d['_id'])),
                                  {"salt": "", "name": d.get('name', ''), "ids": []})
        g["ids"].append(d['_id'])

    n_calls = len(groups)
    logger.info("Deduplicated %d medicines into %d LLM calls (%.1fx fewer calls)",
                total, n_calls, (total / n_calls) if n_calls else 1.0)

    calls = 0
    updated = 0
    for _, g in groups.items():
        calls += 1
        se = _llm_side_effects(g["salt"], g["name"])
        if se:
            res = medicines.update_many(
                {'_id': {'$in': g["ids"]}},
                {'$set': {'side_effects': se, 'side_effects_source': 'llm'}},
            )
            updated += res.modified_count
            logger.info("[%d/%d] salt='%s' name='%s' -> %s (%d docs)",
                        calls, n_calls, g["salt"][:40], g["name"][:30], se, len(g["ids"]))
        else:
            logger.warning("[%d/%d] no result — salt='%s' name='%s'",
                           calls, n_calls, g["salt"][:40], g["name"][:30])
        time.sleep(CALL_DELAY)

    logger.info("COMPLETED. LLM calls: %d, medicines updated: %d/%d",
                calls, updated, total)


if __name__ == "__main__":
    fix_side_effects()
