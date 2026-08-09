#!/usr/bin/env python3
"""
Salt Word-Order Canonicalizer
=============================
Merges the LAST class of phantom salt buckets that `heal_salts.py` deliberately
left alone: two records that carry the SAME molecules written in a different word
ORDER, e.g. `formoterol budesonide` (45 records) vs `budesonide formoterol` (9).

Why heal_salts could not fix these
----------------------------------
The shared `_normalize_salt_key` sorts a composition only when the molecules are
separated by ','/'+'/'and'. When the scraped salt used a plain SPACE between two
molecules ('formoterol budesonide'), the normalizer treats the whole string as a
single molecule token and never reorders it. So the same product splits into a
`formoterol budesonide` bucket and a `budesonide formoterol` bucket, and search
(which `$group`s on normalized_salt) shows them as duplicates.

The safety problem this script must not create
----------------------------------------------
Naively splitting on whitespace and sorting the words would SHATTER real two-word
molecule names — 'clavulanic acid' -> 'acid clavulanic', 'metformin hydrochloride'
-> 'hydrochloride metformin', 'folic acid' -> 'acid folic'. That is exactly the
"wrong data shown to a customer" outcome we are guarding against.

The guarantee
-------------
A real two-word molecule name always ends in a chemical salt-form / counter-ion /
hydrate modifier (acid, hydrochloride, sodium, fumarate, medoxomil, dihydrate,
...). So the rule is deliberately blunt and provably safe:

    Reorder a normalized_salt ONLY when
      * it is 2-5 space-separated words,
      * every word is alphabetic and >=4 chars, and
      * NO word is a known salt-form MODIFIER.

If ANY word is a modifier, the record is left completely untouched (SKIP-MODIFIER)
— which protects every two-word molecule name, because their tail is always a
modifier. Records that pass the guard contain only whole, independent molecule
names, so sorting them is pharmacologically lossless (order does not change a
composition) and can never scramble a molecule's own name.

Per-word INN/USAN synonyms are folded first (acetaminophen -> paracetamol) so a
whitespace-preserved synonym pair ('paracetamol acetaminophen', a scrape artifact)
collapses to the single real molecule.

This is conservative on purpose: combinations that carry a salt-form word
(amoxicillin clavulanic acid, formoterol fumarate budesonide) are left as-is even
though some are order variants — merging those safely needs a molecule-aware
tokenizer, a separate and riskier change. Better a few residual buckets than one
wrong composition on a product page.

Only `normalized_salt` in the medicines collection is touched. Dry-run by default;
`--apply` performs the writes. Take a backup first (heal_salts backup covers the
same field) — this is a production data change with no automatic undo.

Usage:
    python canonicalize_salt_order.py                 # dry-run report (default)
    python canonicalize_salt_order.py --apply         # write reorder corrections
    python canonicalize_salt_order.py --salt budesonide   # scope report to one salt
"""

import os
import sys
import argparse
import logging
from collections import Counter, defaultdict
from typing import List, Tuple
from pymongo import MongoClient
from dotenv import load_dotenv

from medicine_identity import _salt_is_molecular, _SALT_SYNONYMS
from dedup_medicines import _is_garbage_salt

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

HERE = os.path.dirname(os.path.abspath(__file__))

# Chemical salt-form / counter-ion / hydrate / ester modifiers. These are the
# TAIL (or, for a few, the head) of a real multi-word molecule name — never a
# standalone active on their own in this catalog. If a normalized_salt contains
# any of these words, its spaces might be joining a molecule to its own modifier
# ('clavulanic acid', 'metformin hydrochloride', 'olmesartan medoxomil'), so we
# must NOT reorder it. This set is the entire safety mechanism — keep it broad.
_SALT_MODIFIERS = frozenset({
    # acids / the generic tail
    "acid",
    # counter-ion cations (appear before OR after the active)
    "sodium", "disodium", "potassium", "calcium", "magnesium", "zinc",
    "ferrous", "ferric", "aluminium", "aluminum", "lithium",
    # counter-ion anions / simple salts
    "chloride", "bromide", "iodide", "nitrate", "sulfate", "sulphate",
    "phosphate", "carbonate", "bicarbonate", "oxide", "hydroxide",
    "hydrochloride", "dihydrochloride", "hydrobromide", "hcl",
    # organic-acid salts / esters
    "maleate", "mesylate", "besylate", "tartrate", "bitartrate", "citrate",
    "succinate", "fumarate", "propionate", "dipropionate", "valerate",
    "acetate", "acetonide", "furoate", "xinafoate", "gluconate", "orotate",
    "pamoate", "embonate", "decanoate", "enanthate", "palmitate", "stearate",
    "lactate", "aspartate", "glycinate", "picosulfate", "teoclate", "napadisylate",
    "isethionate", "edisylate", "camsylate", "tosylate", "oxalate", "malate",
    # prodrug esters / common two-word tails
    "axetil", "proxetil", "medoxomil", "cilexetil", "mofetil", "fosamil",
    # hydration / crystal state
    "trihydrate", "dihydrate", "hemihydrate", "monohydrate", "anhydrous",
    "dihydrate", "sesquihydrate",
})


def _fold(word: str) -> str:
    """Per-word synonym fold (acetaminophen -> paracetamol). Multi-word synonym
    keys in _SALT_SYNONYMS are ignored here (handled upstream, if ever)."""
    return _SALT_SYNONYMS.get(word, word)


# Verdict tags.
REORDER = "REORDER"              # safe order variant -> canonical sorted form
OK = "OK"                        # already canonical / single molecule
SKIP_MODIFIER = "SKIP-MODIFIER"  # contains a salt-form word -> untouched (2-word safe)
SKIP_SHAPE = "SKIP-SHAPE"        # not 2-5 clean molecule words -> untouched


def classify(normalized_salt: str) -> Tuple[str, str]:
    """Decide whether one normalized_salt is a safely-reorderable order variant.

    Returns (verdict, new_value). new_value is the canonical sorted composition
    for REORDER; "" otherwise.
    """
    stored = (normalized_salt or "").strip()
    if not stored:
        return SKIP_SHAPE, ""

    words = stored.split()
    if not (2 <= len(words) <= 5):
        # Single molecule (nothing to reorder) or long prose/garbage (heal's
        # rescrape bucket) — not ours to touch.
        return (OK, "") if len(words) == 1 else (SKIP_SHAPE, "")

    # Every word must look like a whole molecule name: alphabetic, >=4 chars.
    # A stray digit / short fragment means dose leak or garbage — leave it.
    if not all(w.isalpha() and len(w) >= 4 for w in words):
        return SKIP_SHAPE, ""

    # Reuse the SAME molecular gate heal_salts trusts: reject footer prose that
    # happens to be all-alphabetic ('active substance canagliflozin', 'active
    # ingredients glycomet trio'). Reordering those would only produce equally
    # garbage output — leave them for the rescrape worklist untouched.
    if not _salt_is_molecular(stored) or _is_garbage_salt(stored):
        return SKIP_SHAPE, ""

    # THE GUARD: any salt-form modifier word means a space here may be joining a
    # molecule to its own name-tail ('clavulanic acid'). Never reorder those.
    if any(w in _SALT_MODIFIERS for w in words):
        return SKIP_MODIFIER, ""

    # Safe: all words are independent molecules. Fold synonyms, dedup, sort.
    folded = [_fold(w) for w in words]
    canonical = " ".join(sorted(dict.fromkeys(folded)))
    if canonical != stored:
        return REORDER, canonical
    return OK, ""


def report_and_apply(apply: bool, only_salt: str = ""):
    client = MongoClient(MONGO_URL)
    db = client.get_database("MEDSAVE")
    med_col = db.get_collection("medicines")

    query = {"normalized_salt": {"$regex": " "}}  # only multi-word values matter
    if only_salt:
        query = {"$and": [query,
                          {"normalized_salt": {"$regex": only_salt, "$options": "i"}}]}

    docs = list(med_col.find(query, {"_id": 1, "name": 1,
                                     "normalized_name": 1, "normalized_salt": 1}))
    logger.info(f"Loaded {len(docs)} multi-word normalized_salt records"
                f"{f' matching {only_salt!r}' if only_salt else ''}.")

    counts = Counter()
    fixes: List[Tuple] = []          # (_id, name, old, new)
    # Track which sorted-signatures actually collapse >1 surface order, so the
    # report can show the operator the buckets being merged.
    merged_into = defaultdict(set)   # canonical -> {surface orders seen}
    for d in docs:
        stored = (d.get("normalized_salt") or "").strip()
        verdict, new_val = classify(stored)
        counts[verdict] += 1
        if verdict == REORDER:
            fixes.append((d["_id"], d.get("normalized_name") or d.get("name"),
                          stored, new_val))
            merged_into[new_val].add(stored)
            merged_into[new_val].add(new_val)

    logger.info("=" * 70)
    logger.info("SALT ORDER CANONICALIZATION SUMMARY")
    logger.info(f"  OK (already canonical)   : {counts[OK]}")
    logger.info(f"  REORDER (safe merge)     : {counts[REORDER]}")
    logger.info(f"  SKIP-MODIFIER (2-word)   : {counts[SKIP_MODIFIER]}  [untouched]")
    logger.info(f"  SKIP-SHAPE (dose/prose)  : {counts[SKIP_SHAPE]}  [untouched]")
    logger.info("=" * 70)

    sample = fixes[:20]
    if sample:
        logger.info(f"--- REORDER sample ({len(sample)} of {counts[REORDER]}) ---")
        for _mid, name, old, new in sample:
            logger.info(f"  '{name}'  {old!r} -> {new!r}")

    # Show the buckets that genuinely collapse (>1 distinct order feeding one
    # canonical) — the concrete duplicate-merge proof.
    real_merges = {c: o for c, o in merged_into.items() if len(o) > 1}
    if real_merges:
        logger.info(f"--- buckets merged ({len(real_merges)} canonical groups) ---")
        for canonical in sorted(real_merges, key=lambda c: -len(real_merges[c]))[:20]:
            orders = ", ".join(repr(o) for o in sorted(real_merges[canonical]))
            logger.info(f"  -> {canonical!r}  <=  {orders}")

    if not apply:
        logger.info("=" * 70)
        logger.info(f"DRY-RUN — no writes. {len(fixes)} reorder corrections pending "
                    f"across {len(real_merges)} merged buckets. Re-run with --apply.")
        logger.info("=" * 70)
        client.close()
        return

    logger.warning("APPLYING CHANGES — this modifies production normalized_salt.")
    applied = 0
    for mid, _name, _old, new in fixes:
        med_col.update_one({"_id": mid}, {"$set": {"normalized_salt": new}})
        applied += 1
    logger.info("=" * 70)
    logger.info(f"APPLIED: {applied} normalized_salt values reordered to canonical "
                f"form across {len(real_merges)} merged buckets.")
    logger.info("=" * 70)
    client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Canonicalize space-separated salt word order (dry-run by default).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="execute writes (prod change)")
    ap.add_argument("--salt", default="", metavar="NAME",
                    help="limit the report to normalized_salt matching this string")
    args = ap.parse_args()

    if not MONGO_URL:
        logger.error("MONGO_URL not set — cannot connect.")
        sys.exit(1)

    report_and_apply(apply=args.apply, only_salt=args.salt)
