#!/usr/bin/env python3
"""
Salt Healing Script
===================
Fixes the `normalized_salt` field on STANDALONE medicine records — the gap
`dedup_medicines.py` leaves open.

`dedup_medicines.py` corrects `normalized_salt` only for records inside a
duplicate cluster (via `best_cluster_salt`) or when the field is empty (its
backfill loop). A lone record whose `normalized_salt` is present-but-WRONG (a
stale molecule, a brand stored as the salt, or a dose-leaked value like
'budesonide 0') is never touched — yet search groups on `normalized_salt`
(search_service.py `$group _id: $normalized_salt`), so every such record splits
its product into a phantom bucket and shows up as a "duplicate salt".

This script re-derives the correct `normalized_salt` from each record's raw
`salt` field (the scraped source of truth) using the SAME shared helpers dedup
uses, and reports/repairs four classes:

  BACKFILL  — normalized_salt empty, raw salt yields a molecule.
  RENORM    — stored carries noise (dose leak / stray token); re-normalize it
              (or the raw salt) to the clean molecular form.
  STALE     — stored claims a molecule the raw salt contradicts (disjoint
              tokens); the raw salt wins.
  UNFIXABLE — both stored and raw salt are prose/garbage; nothing to derive.
              These are written to `rescrape_needed.txt` for a targeted refetch
              (no doc is mutated).

A record whose raw salt is a SUPERSET of the stored value (a more complete
combo) is left alone — we never downgrade a combo to a single molecule.

Read-only DRY-RUN by default. `--apply` performs the `normalized_salt` writes.
No schema change: only `normalized_salt` values change, all within the medicines
collection. Prices / history / duplicate merging are dedup's job, not this one.

Usage:
    python heal_salts.py                 # dry-run report (default)
    python heal_salts.py --apply         # write normalized_salt corrections
    python heal_salts.py --salt budesonide   # limit report to one salt bucket
"""

import os
import sys
import argparse
import logging
from collections import Counter
from typing import List, Tuple
from pymongo import MongoClient
from dotenv import load_dotenv

from medicine_identity import _normalize_salt_key, _salt_is_molecular
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
RESCRAPE_FILE = os.path.join(HERE, "rescrape_needed.txt")
CONFLICT_FILE = os.path.join(HERE, "salt_conflicts.txt")

# Non-molecule filler that slips through the shared normalizer because it splits
# a salt only on ','/'+'/'and' — never on spaces — so a prose tail glued to a
# real molecule ('azithromycin as its', 'budesonide product') survives as one
# "molecular" token, and a scrape placeholder ('undefined') reads as a salt. We
# strip these HERE (heal-local) rather than widen the shared markers/garbage sets
# that dedup + the identity resolver + 24 tests depend on.
_STOP = frozenset({
    "as", "its", "their", "is", "the", "and", "for", "with", "of",
    "product", "this", "that", "each", "two", "one", "a", "an",
})


def _mol(salt: str) -> str:
    """Normalize a raw/stored salt to a molecular key, then drop the prose-tail
    and placeholder tokens the shared normalizer can't see (space-glued filler,
    'undefined'). Order-preserving — never shatters a real two-word molecule like
    'clavulanic acid'. Returns '' if nothing molecular remains."""
    key = _normalize_salt_key(salt)
    if not key:
        return ""
    toks = [t for t in key.split() if t not in _STOP and "undefined" not in t]
    return " ".join(toks)


def _clean(salt: str) -> bool:
    """True if a salt string is a usable, molecular composition (not empty,
    prose, or a stray brand token). Combines the identity module's molecular
    check with dedup's garbage filter so this script and dedup agree."""
    return bool(salt) and _salt_is_molecular(salt) and not _is_garbage_salt(salt)


# Verdict tags for a single record's healing decision.
BACKFILL = "BACKFILL"     # stored empty, raw salt yields a molecule
RENORM = "RENORM"         # stored's OWN clean molecular form differs (noise strip)
SALVAGE = "SALVAGE"       # stored unsalvageable; raw salt supplies a molecule
CONFLICT = "CONFLICT"     # stored clean but raw salt disagrees — REVIEW, no write
UNFIXABLE = "UNFIXABLE"   # nothing clean derivable from stored OR raw salt
OK = "OK"                 # stored already clean/canonical — leave it


def classify(doc: dict) -> Tuple[str, str]:
    """Decide how (or whether) to heal one record's normalized_salt.

    Returns (verdict, new_value). new_value is the corrected normalized_salt for
    BACKFILL / RENORM / SALVAGE; the RAW-salt candidate for CONFLICT (report only,
    never written); "" for OK and UNFIXABLE.

    SAFETY PRINCIPLE — the raw scraped `salt` field is the KNOWN-unreliable one
    (mis-matched cross-platform scrapes routinely stamp e.g. brufen with
    'budesonide formoterol'; that is the exact bug the composition sanitiser
    guards against). dedup's `best_cluster_salt` may trust raw salt over stored
    ONLY because a cluster's members corroborate the same product. A STANDALONE
    record has no such corroboration, so here we:

      * PREFER the record's own stored value — the only healing applied to an
        already-clean stored molecule is idempotent re-normalization (strip a
        leaked dose, reorder a combo, fold a synonym). No molecule is ever added
        or swapped from raw salt.
      * reach for raw salt ONLY when stored carries no clean molecule to protect
        (empty -> BACKFILL, garbage/prose -> SALVAGE).
      * when a clean stored molecule and a clean raw salt genuinely DISAGREE,
        emit CONFLICT for human review — we do NOT auto-overwrite good data with
        the unreliable field.
    """
    stored = (doc.get("normalized_salt") or "").strip()
    derived = _mol(doc.get("salt", ""))
    derived_ok = _clean(derived)

    # Case A: stored empty — nothing to protect, backfill from raw salt if clean.
    if not stored:
        if derived_ok:
            return BACKFILL, derived
        return UNFIXABLE, ""

    # Case B: stored already a clean molecule — protect it.
    if _clean(stored):
        # Idempotent cleanup: _mol only strips noise / prose tails / folds
        # synonyms, so its output can never introduce a molecule that wasn't
        # already implied by `stored`. Safe to adopt when it differs.
        renorm = _mol(stored)
        if _clean(renorm) and renorm != stored:
            return RENORM, renorm
        # Cross-check the raw salt only to FLAG a disagreement, never to write.
        if derived_ok and not (set(stored.split()) & set(derived.split())):
            return CONFLICT, derived
        return OK, ""

    # Case C: stored present but dirty (dose leak / prose / stray token).
    # First try to salvage a molecule from the stored value ITSELF — this is the
    # dose-leak majority ('budesonide 0' -> 'budesonide') and needs no raw salt,
    # so it's immune to a raw-salt mis-scrape.
    renorm = _mol(stored)
    if _clean(renorm):
        return RENORM, renorm
    # Stored yields nothing usable; fall back to the raw salt.
    if derived_ok:
        return SALVAGE, derived
    return UNFIXABLE, ""


def report_and_apply(apply: bool, only_salt: str = ""):
    client = MongoClient(MONGO_URL)
    db = client.get_database("MEDSAVE")
    med_col = db.get_collection("medicines")

    query = {}
    if only_salt:
        # Match the requested salt against stored normalized_salt OR raw salt so
        # the report can be scoped to one bucket (e.g. --salt budesonide).
        rx = {"$regex": only_salt, "$options": "i"}
        query = {"$or": [{"normalized_salt": rx}, {"salt": rx}]}

    projection = {"_id": 1, "name": 1, "normalized_name": 1,
                  "salt": 1, "normalized_salt": 1}
    docs = list(med_col.find(query, projection))
    logger.info(f"Loaded {len(docs)} medicine records"
                f"{f' matching {only_salt!r}' if only_salt else ''}.")

    counts = Counter()
    fixes: List[Tuple] = []      # (verdict, _id, name, old, new) — will be written
    conflicts: List[Tuple] = []  # (_id, name, stored, raw_candidate) — review only
    unfixable: List[Tuple] = []
    for d in docs:
        verdict, new_val = classify(d)
        counts[verdict] += 1
        old = (d.get("normalized_salt") or "").strip()
        if verdict in (BACKFILL, RENORM, SALVAGE):
            fixes.append((verdict, d["_id"], d.get("normalized_name") or d.get("name"),
                          old, new_val))
        elif verdict == CONFLICT:
            conflicts.append((d["_id"], d.get("normalized_name") or d.get("name"),
                              old, new_val))
        elif verdict == UNFIXABLE:
            unfixable.append((d["_id"], d.get("normalized_name") or d.get("name"),
                              d.get("salt", ""), old))

    logger.info("=" * 70)
    logger.info("SALT HEALING SUMMARY")
    logger.info(f"  OK (unchanged)      : {counts[OK]}")
    logger.info(f"  BACKFILL (empty)    : {counts[BACKFILL]}")
    logger.info(f"  RENORM  (self-clean): {counts[RENORM]}")
    logger.info(f"  SALVAGE (from raw)  : {counts[SALVAGE]}")
    logger.info(f"  CONFLICT (review)   : {counts[CONFLICT]}  [NOT written]")
    logger.info(f"  UNFIXABLE (rescrape): {counts[UNFIXABLE]}")
    logger.info("=" * 70)

    # Show a sample of each corrective class so the operator can sanity-check
    # before ever passing --apply.
    for tag in (SALVAGE, RENORM, BACKFILL):
        sample = [f for f in fixes if f[0] == tag][:15]
        if sample:
            logger.info(f"--- {tag} sample ({min(15, counts[tag])} of {counts[tag]}) ---")
            for _, mid, name, old, new in sample:
                logger.info(f"  [{mid}] '{name}'  {old!r} -> {new!r}")
    conflict_sample = conflicts[:15]
    if conflict_sample:
        logger.info(f"--- CONFLICT sample ({len(conflict_sample)} of {counts[CONFLICT]}) "
                    f"— stored kept, raw salt shown for review ---")
        for mid, name, stored, raw in conflict_sample:
            logger.info(f"  [{mid}] '{name}'  stored={stored!r}  raw={raw!r}")

    # Always write the rescrape worklist (even in dry-run) — it's a report, not a
    # mutation, and it's what makes the UNFIXABLE bucket actionable.
    if unfixable:
        with open(RESCRAPE_FILE, "w", encoding="utf-8") as fh:
            fh.write("# medicines whose normalized_salt cannot be healed from "
                     "existing data — raw `salt` is prose/garbage too. Rescrape.\n")
            fh.write("# id\tname\traw_salt\tstored_normalized_salt\n")
            for mid, name, raw, old in unfixable:
                raw1 = " ".join(str(raw).split())[:120]
                fh.write(f"{mid}\t{name}\t{raw1}\t{old}\n")
        logger.info(f"Wrote {len(unfixable)} rescrape targets to {RESCRAPE_FILE}")

    # Write the conflict worklist too — these need a human/rescrape call, not an
    # auto-write, so they get their own file rather than mutating anything.
    if conflicts:
        with open(CONFLICT_FILE, "w", encoding="utf-8") as fh:
            fh.write("# clean stored normalized_salt disagrees with the (unreliable) "
                     "raw `salt`. Stored kept as-is; review / rescrape to resolve.\n")
            fh.write("# id\tname\tstored_normalized_salt\traw_salt_candidate\n")
            for mid, name, stored, raw in conflicts:
                fh.write(f"{mid}\t{name}\t{stored}\t{raw}\n")
        logger.info(f"Wrote {len(conflicts)} conflicts to {CONFLICT_FILE}")

    if not apply:
        logger.info("=" * 70)
        logger.info(f"DRY-RUN — no writes. {len(fixes)} normalized_salt corrections "
                    f"pending, {len(conflicts)} conflicts flagged (not written). "
                    f"Re-run with --apply to execute.")
        logger.info("=" * 70)
        client.close()
        return

    logger.warning("APPLYING CHANGES — this modifies production normalized_salt.")
    applied = Counter()
    for verdict, mid, _name, _old, new in fixes:
        med_col.update_one({"_id": mid}, {"$set": {"normalized_salt": new}})
        applied[verdict] += 1
    logger.info("=" * 70)
    logger.info(f"APPLIED: {applied[BACKFILL]} backfilled, {applied[RENORM]} "
                f"re-normalized, {applied[SALVAGE]} salvaged from raw salt "
                f"({sum(applied.values())} total). {len(conflicts)} conflicts and "
                f"{len(unfixable)} unfixable left for review/rescrape.")
    logger.info("=" * 70)
    client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Heal standalone normalized_salt values (dry-run by default).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="execute writes (prod change)")
    ap.add_argument("--salt", default="", metavar="NAME",
                    help="limit the report to records matching this salt "
                         "(stored normalized_salt or raw salt), e.g. --salt budesonide")
    args = ap.parse_args()

    if not MONGO_URL:
        logger.error("MONGO_URL not set — cannot connect.")
        sys.exit(1)

    report_and_apply(apply=args.apply, only_salt=args.salt)
