#!/usr/bin/env python3
"""
Medicine Deduplication & Backfill Script
=========================================
Scans the medicines collection, parses identities with the shared resolver, and
clusters duplicate records using union-find with a salt guard. Then:

  1. Reports every duplicate cluster (same product, >1 record) — for each it
     picks a canonical record (the one with the richest clinical data) and shows
     which records would be merged into it and how many prices move.
  2. Reports records missing `normalized_salt` that can be backfilled from `salt`.

Read-only DRY-RUN by default. `--apply` performs the writes:
  - moves every duplicate's prices to the canonical medicine_id,
  - re-points any user history referencing a dropped medicine_id,
  - deletes the duplicate medicine docs,
  - backfills normalized_salt.

No schema change: only field values + document counts change, all within the
existing medicines / prices / history collections.
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

from medicine_identity import parse_identity, is_same_medicine, _normalize_salt_key

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

# Clinical fields whose presence signals a "richer" record — used to choose the
# canonical doc within a duplicate cluster (keep the most complete one).
_RICHNESS_FIELDS = [
    "salt", "description", "uses", "side_effects", "how_it_works",
    "how_to_use", "manufacturer", "faq", "safety_advice", "fact_box",
]

# Substrings that flag a `salt` value as scraped webpage boilerplate / prose
# rather than an actual composition — such values must never be chosen as the
# canonical normalized_salt.
_SALT_GARBAGE = (
    "view company", "careers blog", "partner with", "our services",
    "order", "composition", "includes", "active", "combination",
    "making it", "proven ingredients", "sufficient", "effective",
)


def _is_garbage_salt(s: str) -> bool:
    """True if a normalized-salt string looks like boilerplate/prose or a stray
    non-salt token, rather than an actual composition."""
    if not s:
        return True
    toks = s.split()
    if len(toks) > 4:                       # real salt keys are 1-4 sorted words
        return True
    if not any(len(t) >= 4 for t in toks):  # no salt-like token (e.g. 'a', 'two')
        return True
    return any(g in s for g in _SALT_GARBAGE)


def best_cluster_salt(docs: List[dict], indices: List[int], canon: int) -> str:
    """Choose the correct normalized_salt for the canonical of a merged cluster.

    The scraped raw `salt` field is the source of truth; `normalized_salt` is a
    derived cache that can be stale or wrong (e.g. the brand stored as the salt,
    'brucare'). So we trust the canonical record's OWN raw salt when it's clean,
    and keep the stored value only when it's consistent with it. This corrects the
    brand-as-salt case ('brucare' -> 'ibuprofen') WITHOUT ever downgrading a good
    salt to a worse one (never 'cefixime' -> 'a', never 'aluminium hydroxide' ->
    'sorbitol'). Falls back to the most complete clean salt among the other
    members only when the canonical itself has none. Returns "" if nothing clean
    is available (leave the record untouched)."""
    stored = (docs[canon].get("normalized_salt") or "").strip()
    derived = _normalize_salt_key(docs[canon].get("salt", ""))
    stored_ok = bool(stored) and not _is_garbage_salt(stored)
    derived_ok = bool(derived) and not _is_garbage_salt(derived)
    if stored_ok and derived_ok:
        # Both clean: keep the already-normalized stored value when it shares a
        # token with the raw-salt-derived one; otherwise stored was stale/wrong
        # (brand-as-salt) and the raw salt wins.
        if set(stored.split()) & set(derived.split()):
            return stored
        return derived
    if derived_ok:
        return derived
    if stored_ok:
        return stored
    # Canonical has no usable salt — take the most complete (most tokens) clean
    # salt among the other cluster members.
    best = ""
    for i in indices:
        for cand in ((docs[i].get("normalized_salt") or "").strip(),
                     _normalize_salt_key(docs[i].get("salt", ""))):
            if (cand and not _is_garbage_salt(cand)
                    and len(cand.split()) > len(best.split())):
                best = cand
    return best


class UnionFind:
    """Minimal union-find over integer indices for clustering duplicates."""

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _richness(doc: dict) -> int:
    """How much clinical data a record carries — higher wins the canonical slot."""
    score = 0
    for f in _RICHNESS_FIELDS:
        v = doc.get(f)
        if v:
            score += len(v) if isinstance(v, (list, dict, str)) else 1
    return score


def load_medicines(col) -> List[dict]:
    """Load only the fields needed for identity + canonical selection (keeps the
    read light — no full clinical blobs)."""
    projection = {
        "_id": 1, "name": 1, "normalized_name": 1, "salt": 1,
        "normalized_salt": 1, "created_at": 1,
    }
    for f in _RICHNESS_FIELDS:
        projection[f] = 1
    docs = list(col.find({}, projection))
    logger.info(f"Loaded {len(docs)} medicine records.")
    return docs


def cluster_duplicates(docs: List[dict]) -> List[List[int]]:
    """Cluster records that resolve to the same medicine identity.

    To avoid an O(n^2) all-pairs comparison over ~17k records, we first bucket by
    brand prefix (cheap, exact) then sub-group by numeric dose only — NOT by form,
    because a form-less name ("brucare 600mg") legitimately matches a form-bearing
    one ("brucare 600mg tablets"); bucketing on form would hide exactly those.

    The "unknown form is a wildcard" rule is not transitive (a form-less record
    could otherwise bridge a tablet and a syrup), so we use a GUARDED union: a
    union is refused if it would put two distinct KNOWN forms in one component.
    That keeps tablet and syrup apart while still letting the form-less record
    attach to one of them."""
    idents = [parse_identity(d.get("normalized_name") or d.get("name") or "",
                             d.get("salt", "")) for d in docs]

    buckets: Dict[str, List[int]] = defaultdict(list)
    for i, ident in enumerate(idents):
        prefix = ident.brand.split(" ")[0] if ident.brand else ""
        buckets[prefix].append(i)

    uf = UnionFind(len(docs))
    # Known forms present in each union-find component, read/written at the root.
    comp_forms: Dict[int, set] = {
        i: ({idents[i].form} if idents[i].form else set()) for i in range(len(docs))
    }

    def guarded_union(a: int, b: int) -> None:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return
        combined = comp_forms[ra] | comp_forms[rb]
        if len(combined) > 1:  # would span two distinct known forms — refuse
            return
        uf.union(a, b)
        comp_forms[uf.find(a)] = combined

    comparisons = 0
    for prefix, members in buckets.items():
        if not prefix or len(members) < 2:
            continue
        sub: Dict[tuple, List[int]] = defaultdict(list)
        for i in members:
            sub[idents[i].strength_nums].append(i)
        for key, group in sub.items():
            for a in range(len(group)):
                for b in range(a + 1, len(group)):
                    comparisons += 1
                    if is_same_medicine(idents[group[a]], idents[group[b]]):
                        guarded_union(group[a], group[b])

    logger.info(f"Identity comparisons run: {comparisons}")
    clusters: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(docs)):
        clusters[uf.find(i)].append(i)
    return [c for c in clusters.values() if len(c) > 1]


def pick_canonical(docs: List[dict], indices: List[int]) -> int:
    """Choose the index to keep: richest clinical data, tie-broken by oldest."""
    def sort_key(i):
        created = docs[i].get("created_at") or datetime.max
        return (-_richness(docs[i]), created)
    return sorted(indices, key=sort_key)[0]


def report_and_apply(apply: bool):
    client = MongoClient(MONGO_URL)
    db = client.get_database("MEDSAVE")
    med_col = db.get_collection("medicines")
    price_col = db.get_collection("prices")
    hist_col = db.get_collection("history")

    docs = load_medicines(med_col)
    by_index_id = {i: docs[i]["_id"] for i in range(len(docs))}

    # ── 1. Duplicate clusters ──────────────────────────────────────────────
    dup_clusters = cluster_duplicates(docs)
    total_dupes = sum(len(c) - 1 for c in dup_clusters)
    logger.info("=" * 70)
    logger.info(f"DUPLICATE CLUSTERS: {len(dup_clusters)} clusters, "
                f"{total_dupes} records would be merged away.")
    logger.info("=" * 70)

    merge_ops = []  # (canonical_id, [dup_ids], canon_salt, canon_cur_salt)
    salt_corrections = 0
    for cluster in dup_clusters:
        canon = pick_canonical(docs, cluster)
        canon_id = by_index_id[canon]
        dup_ids = [by_index_id[i] for i in cluster if i != canon]
        price_moves = price_col.count_documents({"medicine_id": {"$in": dup_ids}})
        canon_cur_salt = (docs[canon].get("normalized_salt") or "").strip()
        canon_salt = best_cluster_salt(docs, cluster, canon)
        merge_ops.append((canon_id, dup_ids, canon_salt, canon_cur_salt))
        logger.info(
            f"  KEEP  [{canon_id}] '{docs[canon].get('normalized_name')}' "
            f"(richness={_richness(docs[canon])})")
        for i in cluster:
            if i != canon:
                logger.info(
                    f"    MERGE [{by_index_id[i]}] "
                    f"'{docs[i].get('normalized_name')}' "
                    f"(richness={_richness(docs[i])})")
        logger.info(f"    -> {price_moves} price rows move to canonical")
        if canon_salt and canon_salt != canon_cur_salt:
            salt_corrections += 1
            logger.info(f"    -> normalized_salt {canon_cur_salt!r} -> {canon_salt!r}")

    # ── 2. Missing normalized_salt backfill ────────────────────────────────
    backfill = []
    for i, d in enumerate(docs):
        cur = d.get("normalized_salt")
        derived = _normalize_salt_key(d.get("salt", ""))
        if (not cur) and derived:
            backfill.append((by_index_id[i], derived))
    logger.info("=" * 70)
    logger.info(f"NORMALIZED_SALT BACKFILL: {len(backfill)} records "
                f"have empty normalized_salt but a derivable value.")
    logger.info("=" * 70)
    for mid, val in backfill[:20]:
        logger.info(f"  [{mid}] normalized_salt <- '{val}'")
    if len(backfill) > 20:
        logger.info(f"  ... and {len(backfill) - 20} more")

    # ── 3. Apply (guarded) ─────────────────────────────────────────────────
    if not apply:
        logger.info("=" * 70)
        logger.info("DRY-RUN — no writes performed. Re-run with --apply to execute.")
        logger.info(f"SUMMARY: {len(dup_clusters)} dup clusters "
                    f"({total_dupes} records to remove), "
                    f"{salt_corrections} canonical salt corrections, "
                    f"{len(backfill)} empty-salt backfills.")
        logger.info("=" * 70)
        client.close()
        return

    logger.warning("APPLYING CHANGES — this modifies production data.")
    moved_prices = deleted_docs = repointed_hist = backfilled = dropped_prices = 0
    salts_fixed = 0

    for canon_id, dup_ids, canon_salt, canon_cur_salt in merge_ops:
        # Prices carry a unique (medicine_id, platform) index, so we can't blindly
        # re-point — that would collide where canonical already has that platform.
        # Keep canonical's own price per platform; for a duplicate's price, move it
        # only if canonical lacks that platform, otherwise drop it (stale dup copy;
        # the next scrape refreshes canonical anyway).
        canon_platforms = set(
            price_col.distinct("platform", {"medicine_id": canon_id}))
        all_dup_ids = dup_ids + [str(x) for x in dup_ids]  # ObjectId + string refs
        for price in price_col.find({"medicine_id": {"$in": all_dup_ids}}):
            plat = price.get("platform")
            if plat in canon_platforms:
                price_col.delete_one({"_id": price["_id"]})
                dropped_prices += 1
            else:
                price_col.update_one(
                    {"_id": price["_id"]},
                    {"$set": {"medicine_id": canon_id}})
                canon_platforms.add(plat)
                moved_prices += 1
        # Re-point user history so nobody's saved medicine 404s (both ref types).
        hres = hist_col.update_many(
            {"medicine_id": {"$in": all_dup_ids}},
            {"$set": {"medicine_id": canon_id}})
        repointed_hist += hres.modified_count
        dres = med_col.delete_many({"_id": {"$in": dup_ids}})
        deleted_docs += dres.deleted_count
        # Correct the canonical's normalized_salt (fixes 'brucare'->'ibuprofen'
        # and None->'ibuprofen' so the merged product groups correctly on search).
        if canon_salt and canon_salt != canon_cur_salt:
            med_col.update_one({"_id": canon_id},
                               {"$set": {"normalized_salt": canon_salt}})
            salts_fixed += 1

    for mid, val in backfill:
        med_col.update_one({"_id": mid}, {"$set": {"normalized_salt": val}})
        backfilled += 1

    logger.info("=" * 70)
    logger.info(f"APPLIED: moved {moved_prices} prices, dropped "
                f"{dropped_prices} redundant dup prices, re-pointed "
                f"{repointed_hist} history rows, deleted {deleted_docs} "
                f"duplicate medicines, corrected {salts_fixed} canonical salts, "
                f"backfilled {backfilled} empty salts.")
    logger.info("=" * 70)
    client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dedup & backfill medicines (dry-run by default).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="execute writes (prod change)")
    args = ap.parse_args()

    if not MONGO_URL:
        logger.error("MONGO_URL not set — cannot connect.")
        sys.exit(1)

    report_and_apply(apply=args.apply)
