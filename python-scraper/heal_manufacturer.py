#!/usr/bin/env python3
"""
Manufacturer Healing Script
===========================
Blanks FALSE, platform-self-named `manufacturer` values on customer-facing
medicine records so a pharmacy's own listing label never appears where the real
drug maker should.

The problem
-----------
Several platform scrapers used to stamp their OWN name into the manufacturer
field when the page did not expose the real maker:

  * apollo.py        -> "Apollo Partner"   (hardcoded, never a real maker)
  * amazon_pharmacy  -> "Amazon Seller"    (hardcoded marketplace label)
  * medplus.py       -> "Medplus"          (default when the tile had no maker)
  * truemeds.py      -> "Truemeds"         (API fallback)
  * platinumrx.py    -> "PlatinumRx"       (API fallback)

Apollo in particular white-labels the field on their site, so a medicine really
made by e.g. Mankind was recorded as "Apollo Pharmacy". The scrapers have since
been fixed to emit "" instead of a platform name, but records written before that
fix still carry the wrong value. Search / medicine / compare pages read
`medicines.manufacturer` directly (medicine_service.py `"manufacturer": "$manufacturer"`),
so a stale platform label is shown to the customer as the maker — the exact
"wrong data to customer" outcome to avoid.

What this script does
----------------------
Scans `medicines.manufacturer`. When the stored value is ONLY a platform self-name
(exact, case-insensitive match against the known white-label set — NOT a substring
match, so a genuine maker like "Apollo Hospitals Enterprise" is never touched), it
blanks the field to "". A blank field renders as "Information Not Available"
downstream (medicine_service.py `$ifNull`), which is honest; a wrong maker is not.

Blanked records are also written to `manufacturer_rescrape.txt` so the real maker
can be refetched from the 1mg / netmeds detail page (JSON-LD `marketer.legalName`),
which is the trustworthy source the scraper now uses.

SAFETY
------
* Only EXACT (whole-string, case-insensitive) matches against the platform set are
  changed. Partial/substring matches are left alone.
* A record whose manufacturer is any real company name is never touched.
* Blanking is loss-safe: the true maker was never in this field for these records,
  and the raw value is preserved in the rescrape worklist for recovery.
* Read-only DRY-RUN by default. `--apply` performs the writes. Take a backup of the
  `manufacturer` field first (this is a production data change with no auto-undo).

Usage:
    python heal_manufacturer.py                 # dry-run report (default)
    python heal_manufacturer.py --apply         # blank false platform manufacturers
    python heal_manufacturer.py --backup out.json   # dump manufacturer field first
"""

import os
import sys
import json
import argparse
import logging
from collections import Counter
from typing import List, Tuple
from pymongo import MongoClient
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

HERE = os.path.dirname(os.path.abspath(__file__))
RESCRAPE_FILE = os.path.join(HERE, "manufacturer_rescrape.txt")

# Platform self-names that are NEVER a real drug maker. Matched EXACTLY (whole
# string, case-insensitive, whitespace-trimmed) — never as a substring — so a
# legitimate company name that merely contains one of these words is untouched.
# Keep this list to strings a platform scraper actually stamped as a fallback.
_PLATFORM_LABELS = frozenset({
    "apollo partner",
    "apollo pharmacy",
    "apollo",
    "amazon seller",
    "amazon pharmacy",
    "amazon",
    "medplus",
    "truemeds",
    "platinumrx",
    "platinum rx",
    "netmeds",
    "1mg",
    "tata 1mg",
    "pharmeasy",
    # Bare placeholders that some rows carry instead of a maker.
    "n/a",
    "na",
    "unknown",
    "information not available",
})


def is_false_manufacturer(value: str) -> bool:
    """True if a stored manufacturer is only a platform self-name / placeholder,
    matched as a whole trimmed string (case-insensitive). Substring matches do
    NOT count, so 'Apollo Hospitals Enterprise Ltd' is a real maker and stays."""
    if not value:
        return False
    return value.strip().lower() in _PLATFORM_LABELS


def backup_manufacturer(med_col, path: str):
    rows = [{"_id": str(d["_id"]),
             "name": d.get("normalized_name") or d.get("name"),
             "manufacturer": d.get("manufacturer", "")}
            for d in med_col.find({}, {"name": 1, "normalized_name": 1,
                                       "manufacturer": 1})]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=0)
    logger.info(f"Backed up manufacturer field for {len(rows)} records to {path}")


def report_and_apply(apply: bool):
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=30000)
    db = client.get_database("MEDSAVE")
    med_col = db.get_collection("medicines")

    docs = list(med_col.find({"manufacturer": {"$nin": ["", None]}},
                             {"_id": 1, "name": 1, "normalized_name": 1,
                              "manufacturer": 1}))
    logger.info(f"Loaded {len(docs)} records with a non-empty manufacturer.")

    counts = Counter()
    fixes: List[Tuple] = []  # (_id, name, old_value)
    label_hits = Counter()
    for d in docs:
        mfr = (d.get("manufacturer") or "").strip()
        if is_false_manufacturer(mfr):
            counts["FALSE"] += 1
            label_hits[mfr.lower()] += 1
            fixes.append((d["_id"], d.get("normalized_name") or d.get("name"), mfr))
        else:
            counts["REAL"] += 1

    logger.info("=" * 70)
    logger.info("MANUFACTURER HEALING SUMMARY")
    logger.info(f"  REAL (kept)              : {counts['REAL']}")
    logger.info(f"  FALSE platform label     : {counts['FALSE']}  [blank -> rescrape]")
    logger.info("=" * 70)
    if label_hits:
        logger.info("--- false-label breakdown ---")
        for label, n in label_hits.most_common():
            logger.info(f"  {n:6d}  {label!r}")

    sample = fixes[:20]
    if sample:
        logger.info(f"--- blank sample ({len(sample)} of {counts['FALSE']}) ---")
        for _mid, name, old in sample:
            logger.info(f"  '{name}'  {old!r} -> ''")

    # Always write the rescrape worklist (even in dry-run) — it is a report, and
    # it is what makes the blanked records recoverable via a detail-page refetch.
    if fixes:
        with open(RESCRAPE_FILE, "w", encoding="utf-8") as fh:
            fh.write("# medicines whose manufacturer was a platform self-name / "
                     "placeholder, not the real maker. Blanked; rescrape the real\n")
            fh.write("# manufacturer from the 1mg/netmeds detail page "
                     "(JSON-LD marketer.legalName).\n")
            fh.write("# id\tname\tblanked_value\n")
            for mid, name, old in fixes:
                fh.write(f"{mid}\t{name}\t{old}\n")
        logger.info(f"Wrote {len(fixes)} rescrape targets to {RESCRAPE_FILE}")

    if not apply:
        logger.info("=" * 70)
        logger.info(f"DRY-RUN — no writes. {len(fixes)} false manufacturers would be "
                    f"blanked. Re-run with --apply to execute.")
        logger.info("=" * 70)
        client.close()
        return

    logger.warning("APPLYING CHANGES — this blanks production manufacturer values.")
    applied = 0
    for mid, _name, _old in fixes:
        med_col.update_one({"_id": mid}, {"$set": {"manufacturer": ""}})
        applied += 1
    logger.info("=" * 70)
    logger.info(f"APPLIED: {applied} false manufacturers blanked. "
                f"{len(fixes)} rescrape targets recorded.")
    logger.info("=" * 70)
    client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Blank false platform-name manufacturers (dry-run by default).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="execute writes (prod change)")
    ap.add_argument("--backup", default="", metavar="PATH",
                    help="dump the manufacturer field of every record to PATH first")
    args = ap.parse_args()

    if not MONGO_URL:
        logger.error("MONGO_URL not set — cannot connect.")
        sys.exit(1)

    if args.backup:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=30000)
        backup_manufacturer(client.get_database("MEDSAVE").get_collection("medicines"),
                            args.backup)
        client.close()

    report_and_apply(apply=args.apply)
