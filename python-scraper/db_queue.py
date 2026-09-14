"""
MedLens Scraper — Database-Backed Task Queue
=============================================
Stateless task queue for GitHub Actions workers with atomic claim semantics.
Enforces:
- Hard Rule 2: Multi-document transactions required in production.
- Hard Rule 3: Price observation monotonicity (stale/out-of-order writes rejected).
- Atomic claim ownership with heartbeat-based stale recovery.
"""

import os
import sys
import uuid
import logging
from datetime import datetime, timedelta
from pymongo import ReturnDocument, WriteConcern

logger = logging.getLogger(__name__)


def compute_hot_priority_score(demand_7d: int, demand_prior_7d: int, staleness_hours: float) -> float:
    """
    Demand-driven hot priority formula with smoothing and volume floors.
    - Floor: demand_7d < 10 -> trend_multiplier = 1.0
    - Laplace smoothing: (demand_7d + 10) / (demand_prior_7d + 10)
    - Cap: min(max(raw_trend, 0.5), 2.5)
    - Score = (demand_7d * trend_multiplier) + (0.5 * staleness_hours)
    """
    if demand_7d < 10:
        trend_multiplier = 1.0
    else:
        raw_trend = (demand_7d + 10.0) / (demand_prior_7d + 10.0)
        trend_multiplier = max(0.5, min(2.5, raw_trend))
        
    return float((demand_7d * trend_multiplier) + (0.5 * staleness_hours))


def initialize_sync_states_if_needed(db, total_shards: int = 4):
    """
    Ensures every medicine document in `medicines` has a corresponding
    `medicine_sync_state` record. Shard IDs are deterministically distributed.
    Uses memory-efficient streaming to avoid large Mongo $nin query limits.
    """
    logger.info("Verifying operational sync state synchronization...")
    existing_count = db.medicine_sync_state.count_documents({})
    total_meds = db.medicines.count_documents({})
    if existing_count >= total_meds and total_meds > 0:
        logger.info("Operational sync state fully initialized (%d / %d records).", existing_count, total_meds)
        return

    logger.info("Fetching existing sync state IDs (%d existing)...", existing_count)
    existing_state_ids = {doc["_id"] for doc in db.medicine_sync_state.find({}, {"_id": 1})}
    
    inserts = []
    total_inserted = 0
    
    cursor = db.medicines.find({}, {"_id": 1})
    for doc in cursor:
        med_id = doc["_id"]
        if med_id in existing_state_ids:
            continue
            
        # Deterministic shard assignment based on ObjectId hash
        shard_id = int(str(med_id)[-4:], 16) % total_shards
        inserts.append({
            "_id": med_id,
            "shard_id": shard_id,
            "sync_status": "READY",
            "sync_tier": "warm",
            "sync_run_id": None,
            "sync_started_at": None,
            "heartbeat_at": None,
            "last_synced_at": None,
            "last_sync_duration_ms": 0,
            "sync_failure_count": 0,
            "last_failure_reason": None,
            "platform_coverage": 0,
            "version": 1
        })
        if len(inserts) >= 1000:
            db.medicine_sync_state.insert_many(inserts, ordered=False)
            total_inserted += len(inserts)
            existing_state_ids.update(d["_id"] for d in inserts)
            logger.info("Initialized %d / %d medicine_sync_state records...", total_inserted, total_meds - existing_count)
            inserts = []
            
    if inserts:
        db.medicine_sync_state.insert_many(inserts, ordered=False)
        total_inserted += len(inserts)
        logger.info("Sync state initialization complete: inserted %d records.", total_inserted)


def recover_stale_in_progress(db, stale_timeout_sec: int = 1200):
    """Sweeps expired IN_PROGRESS tasks whose runner crashed or timed out."""
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=stale_timeout_sec)
    
    res = db.medicine_sync_state.with_options(write_concern=WriteConcern(w="majority")).update_many(
        {
            "sync_status": "IN_PROGRESS",
            "heartbeat_at": {"$lt": stale_cutoff}
        },
        {
            "$set": {
                "sync_status": "READY",
                "sync_run_id": None,
                "last_failure_reason": "HEARTBEAT_TIMEOUT_RECOVERED"
            },
            "$inc": {"sync_failure_count": 1, "version": 1}
        }
    )
    if res.modified_count > 0:
        logger.warning(f"Swept and recovered {res.modified_count} stale runner claims.")


def claim_batch_atomic(db, shard_id: int, run_id: str, batch_size: int = 10, stale_timeout_sec: int = 1200) -> list:
    """
    Atomically claims next READY batch for shard_id.
    """
    recover_stale_in_progress(db, stale_timeout_sec)
    now = datetime.utcnow()
    claimed_ids = []
    
    for _ in range(batch_size):
        doc = db.medicine_sync_state.with_options(write_concern=WriteConcern(w="majority")).find_one_and_update(
            {
                "shard_id": shard_id,
                "sync_status": "READY"
            },
            {
                "$set": {
                    "sync_status": "IN_PROGRESS",
                    "sync_run_id": run_id,
                    "sync_started_at": now,
                    "heartbeat_at": now
                },
                "$inc": {"version": 1}
            },
            sort=[("last_synced_at", 1)],
            return_document=ReturnDocument.AFTER
        )
        if not doc:
            break
        claimed_ids.append(doc["_id"])
    return claimed_ids


def claim_hot_batch_atomic(db, run_id: str, batch_size: int = 250, stale_timeout_sec: int = 1200) -> list:
    """
    Selects and claims top priority hot medicines derived from 7-day demand and staleness.
    """
    recover_stale_in_progress(db, stale_timeout_sec)
    now = datetime.utcnow()
    seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    fourteen_days_ago = (now - timedelta(days=14)).strftime("%Y-%m-%d")

    # Aggregate 7d and prior 7d demand per medicine
    pipeline = [
        {"$match": {"date": {"$gte": fourteen_days_ago}}},
        {
            "$group": {
                "_id": "$medicine_id",
                "demand_7d": {
                    "$sum": {
                        "$cond": [{"$gte": ["$date", seven_days_ago]}, "$search_hits", 0]
                    }
                },
                "demand_prior_7d": {
                    "$sum": {
                        "$cond": [{"$lt": ["$date", seven_days_ago]}, "$search_hits", 0]
                    }
                }
            }
        },
        {"$match": {"demand_7d": {"$gt": 0}}},
        {"$sort": {"demand_7d": -1}},
        {"$limit": 500}
    ]
    
    hot_demand = list(db.demand_daily_buckets.aggregate(pipeline))
    candidate_scores = []
    
    for item in hot_demand:
        med_id = item["_id"]
        d7 = item.get("demand_7d", 0)
        dp = item.get("demand_prior_7d", 0)
        
        state = db.medicine_sync_state.find_one({"_id": med_id, "sync_status": "READY"})
        if state:
            last_sync = state.get("last_synced_at")
            staleness_h = (now - last_sync).total_seconds() / 3600.0 if last_sync else 168.0
            score = compute_hot_priority_score(d7, dp, staleness_h)
            candidate_scores.append((score, med_id))
            
    candidate_scores.sort(key=lambda x: x[0], reverse=True)
    target_ids = [cid for _, cid in candidate_scores[:batch_size]]
    
    claimed = []
    for med_id in target_ids:
        doc = db.medicine_sync_state.with_options(write_concern=WriteConcern(w="majority")).find_one_and_update(
            {"_id": med_id, "sync_status": "READY"},
            {
                "$set": {
                    "sync_status": "IN_PROGRESS",
                    "sync_run_id": run_id,
                    "sync_started_at": now,
                    "heartbeat_at": now,
                    "sync_tier": "hot"
                },
                "$inc": {"version": 1}
            },
            return_document=ReturnDocument.AFTER
        )
        if doc:
            claimed.append(doc["_id"])
    return claimed


def update_heartbeat(db, med_id, run_id: str):
    """Keep-alive heartbeat for long-running sync operations."""
    db.medicine_sync_state.update_one(
        {"_id": med_id, "sync_run_id": run_id, "sync_status": "IN_PROGRESS"},
        {"$set": {"heartbeat_at": datetime.utcnow()}}
    )


def finalize_sync_atomic(client, db, med_id, run_id: str, price_entries: list, history_rows: list, duration_ms: int) -> tuple[bool, str]:
    """
    Atomically verifies sync_run_id ownership and commits prices inside an ACID transaction.
    Enforces:
    - Hard Rule 2 (Transactions Required): Standalone MongoDB without transaction support is rejected.
    - Hard Rule 3 (Observation Monotonicity): Older price observations never overwrite stored prices.
    """
    now = datetime.utcnow()
    
    # Rule 2: Standalone MongoDB is rejected in production. Transactions are strictly required.
    try:
        if not (client.is_primary or client.is_mongos):
            return False, "TRANSACTIONS_REQUIRED"
    except Exception:
        return False, "TRANSACTIONS_REQUIRED"

    wc_majority = WriteConcern(w="majority")
    
    with client.start_session() as session:
        try:
            with session.start_transaction(write_concern=wc_majority):
                # 1. Atomic claim check
                res = db.medicine_sync_state.with_options(write_concern=wc_majority).update_one(
                    {
                        "_id": med_id,
                        "sync_run_id": run_id,
                        "sync_status": "IN_PROGRESS"
                    },
                    {
                        "$set": {
                            "sync_status": "SUCCESS",
                            "sync_run_id": None,
                            "last_synced_at": now,
                            "last_sync_duration_ms": duration_ms,
                            "sync_failure_count": 0,
                            "last_failure_reason": None,
                            "platform_coverage": len(price_entries)
                        },
                        "$inc": {"version": 1}
                    },
                    session=session
                )
                if res.matched_count == 0:
                    session.abort_transaction()
                    return False, "OWNERSHIP_LOST_ABORTED"
                
                # 2. Write prices with Rule 3 (Observation Monotonicity)
                _write_prices_internal(db, med_id, run_id, price_entries, history_rows, now, session=session)
                return True, "COMMITTED_TRANSACTION"
        except Exception as e:
            session.abort_transaction()
            logger.error(f"Transaction aborted for {med_id}: {e}")
            return False, f"TRANSACTION_FAILED: {str(e)}"


def _write_prices_internal(db, med_id, run_id, price_entries, history_rows, now, session=None):
    wc = WriteConcern(w="majority")
    for p in price_entries:
        # Rule 3: Observation Monotonicity Check
        existing_price = db.prices.find_one(
            {"medicine_id": med_id, "platform": p["platform"]},
            {"scraped_at": 1},
            session=session
        )
        if existing_price and existing_price.get("scraped_at"):
            if p["scraped_at"] <= existing_price["scraped_at"]:
                # Incoming observation is older than or equal to stored observation; reject overwrite
                continue

        p["sync_run_id"] = run_id
        p["updated_at"] = now
        db.prices.with_options(write_concern=wc).update_one(
            {"medicine_id": med_id, "platform": p["platform"]},
            {"$set": p},
            upsert=True,
            session=session
        )
    if history_rows:
        for h in history_rows:
            h["sync_run_id"] = run_id
        db.price_history.with_options(write_concern=wc).insert_many(history_rows, session=session)


def mark_sync_failure(db, med_id, run_id: str, error_reason: str):
    """Marks task as failed or ready for backoff retry."""
    now = datetime.utcnow()
    state = db.medicine_sync_state.find_one({"_id": med_id, "sync_run_id": run_id})
    if not state:
        return
        
    failures = state.get("sync_failure_count", 0) + 1
    new_status = "FAILED" if failures >= 5 else "READY"
    
    db.medicine_sync_state.with_options(write_concern=WriteConcern(w="majority")).update_one(
        {"_id": med_id, "sync_run_id": run_id},
        {
            "$set": {
                "sync_status": new_status,
                "sync_run_id": None,
                "last_failure_reason": str(error_reason)[:500],
                "heartbeat_at": now
            },
            "$inc": {"sync_failure_count": 1, "version": 1}
        }
    )
