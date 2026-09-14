"""
MedLens Architecture — Comprehensive Live Dry-Run Suite
======================================================
Tests all components against the active environment:
1. Atlas connectivity & multi-document transaction support
2. Index definitions on all 7 collections
3. Search analytics buffer & query classifier (medSave-India)
4. Database task queue atomic claim & stale recovery (medLensScrapping)
5. Atomic transaction finalize & observation monotonicity (Hard Rules 2 & 3)
6. Staging ingestion gate & canonical promotion (Hard Rule 5)
7. Scheduled runner CLI dry-run for hot, shard, and discover modes
"""

import os
import sys
import time
import uuid
import logging
from datetime import datetime, timedelta
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("dry_run")

# Load environment
scraper_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(scraper_dir, ".env"))
load_dotenv(os.path.join(scraper_dir, "..", ".env"))
MONGO_URL = os.getenv("MONGO_URL")

medsave_backend_dir = os.path.abspath(os.path.join(scraper_dir, "..", "..", "..", "medSave-India", "backend"))
if medsave_backend_dir not in sys.path:
    sys.path.insert(0, medsave_backend_dir)
if scraper_dir not in sys.path:
    sys.path.insert(0, scraper_dir)

results = []

def record_test(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    logger.info(f"[{status}] {name} - {detail}")
    results.append({"name": name, "passed": passed, "detail": detail})

def run_dry_run_suite():
    logger.info("=" * 75)
    logger.info("STARTING COMPREHENSIVE ARCHITECTURE DRY-RUN")
    logger.info("=" * 75)
    
    if not MONGO_URL:
        logger.error("MONGO_URL environment variable is missing.")
        sys.exit(1)
        
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)
    db = client.get_database("MEDSAVE")
    
    # ── Test 1: Cluster & ACID Transaction Capability ──
    try:
        ping_res = client.admin.command("ping")
        is_primary = client.is_primary
        is_mongos = client.is_mongos
        supports_tx = bool(is_primary or is_mongos)
        record_test("Atlas Ping & Transactions", supports_tx, f"ping={ping_res}, is_primary={is_primary}, tx_ready={supports_tx}")
    except Exception as e:
        record_test("Atlas Ping & Transactions", False, str(e))
        return

    # ── Test 2: Index Verification on All Collections ──
    try:
        from db import ensure_indexes
        idx_ok = ensure_indexes()
        
        # Verify critical collections exist
        cols = db.list_collection_names()
        expected_cols = [
            "medicines", "prices", "price_history", "medicine_sync_state",
            "staging_medicines", "search_events", "demand_daily_buckets", "search_misses"
        ]
        all_cols_present = all(c in cols for c in expected_cols)
        record_test("Indexes & Collections", all_cols_present, f"Collections present: {expected_cols}")
    except Exception as e:
        record_test("Indexes & Collections", False, str(e))

    # ── Test 3: Search Miss Intent Classification ──
    try:
        from query_classifier import classify_search_miss
        
        c1 = classify_search_miss("telmisartan 40 mg tablet")
        c2 = classify_search_miss("severe headache and vomiting")
        c3 = classify_search_miss("how to store insulin pen")
        c4 = classify_search_miss("paracetaml 500")
        c5 = classify_search_miss("asdfghjkl")
        
        classifier_ok = (
            c1 == "QUALIFIED_MEDICINE"
            and c2 == "SYMPTOM"
            and c3 == "INFORMATIONAL"
            and c4 == "QUALIFIED_MEDICINE"
            and c5 == "SPAM"
        )
        record_test("Query Intent Classifier", classifier_ok,
                    f"Meds={c1}, Symptom={c2}, Info={c3}, Typo/Token={c4}, Spam={c5}")
    except Exception as e:
        record_test("Query Intent Classifier", False, str(e))

    # ── Test 4: Analytics Batch Drainer & Non-Blocking Flush (Hard Rules 1 & 4) ──
    try:
        from analytics_queue import AnalyticsBatchDrainer
        test_source = f"dryrun_{uuid.uuid4().hex[:6]}"
        drainer = AnalyticsBatchDrainer(db_getter=lambda: db, batch_size=10, flush_interval_sec=0.1, shutdown_timeout_sec=5.0)
        
        # Record search hit event
        test_med_id = ObjectId()
        drainer.record_search_event(
            raw_query="Dolo 650",
            norm_query="dolo 650",
            result_count=3,
            matched_ids=[test_med_id],
            query_source=test_source
        )
        
        # Record a qualified medicine search miss (Hard Rule 1 non-blocking)
        drainer.record_search_miss(
            raw_query="DryRunNovartisTest 500mg",
            norm_query="dryrunnovartistest 500mg"
        )
        
        # Bounded shutdown flush (Hard Rule 4)
        drainer.flush_and_stop()
        
        # Verify search event was stored
        evt = db.search_events.find_one({"normalized_query": "dolo 650", "query_source": test_source})
        miss = db.search_misses.find_one({"normalized_query": "dryrunnovartistest 500mg"})
        daily_bucket = db.demand_daily_buckets.find_one({"medicine_id": test_med_id})
        
        drainer_ok = (evt is not None) and (miss is not None) and (daily_bucket is not None) and (miss.get("intent") == "QUALIFIED_MEDICINE")
        record_test("Analytics Drainer & Miss Storage (Rules 1 & 4)", drainer_ok,
                    f"SearchEvent={bool(evt)}, SearchMiss={bool(miss)}, DailyBucket={bool(daily_bucket)}, Intent={miss.get('intent') if miss else None}")
        
        # Clean up test artifacts
        db.search_events.delete_many({"query_source": test_source})
        db.search_misses.delete_many({"normalized_query": "dryrunnovartistest 500mg"})
        db.demand_daily_buckets.delete_many({"medicine_id": test_med_id})
    except Exception as e:
        record_test("Analytics Drainer & Miss Storage (Rules 1 & 4)", False, str(e))

    # ── Test 5: Operational Task Queue Initialization & Atomic Claim ──
    try:
        import db_queue
        
        # Initialize sync state records if needed
        db_queue.initialize_sync_states_if_needed(db, total_shards=4)
        sync_state_count = db.medicine_sync_state.count_documents({})
        med_count = db.medicines.count_documents({})
        init_ok = (sync_state_count >= med_count and med_count > 0)
        record_test("Sync State Initialization", init_ok, f"{sync_state_count} sync_state records for {med_count} medicines")
        
        # Test shard claim
        test_run_id = f"test_shard_{uuid.uuid4().hex[:6]}"
        claimed_shard = db_queue.claim_batch_atomic(db, shard_id=0, run_id=test_run_id, batch_size=2)
        shard_claim_ok = len(claimed_shard) == 2
        record_test("Atomic Shard Claim (db_queue)", shard_claim_ok, f"Claimed {len(claimed_shard)} items for shard 0: {claimed_shard}")
        
        # Verify in_progress status in DB
        claimed_docs = list(db.medicine_sync_state.find({"_id": {"$in": claimed_shard}}))
        status_ok = all(d["sync_status"] == "IN_PROGRESS" and d["sync_run_id"] == test_run_id for d in claimed_docs)
        record_test("Claim Lease Status Verification", status_ok, "All claimed docs have IN_PROGRESS and correct sync_run_id")
        
        # Test Stale Claim Recovery
        db.medicine_sync_state.update_one(
            {"_id": claimed_shard[0]},
            {"$set": {"heartbeat_at": datetime.utcnow() - timedelta(seconds=2000)}}
        )
        db_queue.recover_stale_in_progress(db, stale_timeout_sec=1200)
        recovered_doc = db.medicine_sync_state.find_one({"_id": claimed_shard[0]})
        recovery_ok = (recovered_doc["sync_status"] == "READY" and recovered_doc["last_failure_reason"] == "HEARTBEAT_TIMEOUT_RECOVERED")
        record_test("Stale Claim Recovery", recovery_ok, f"Recovered stale item back to status: {recovered_doc.get('sync_status')}")
        
        # Release the second claim back to READY
        db.medicine_sync_state.update_one(
            {"_id": claimed_shard[1]},
            {"$set": {"sync_status": "READY", "sync_run_id": None}}
        )
    except Exception as e:
        record_test("Task Queue Operations", False, str(e))

    # ── Test 6: Multi-Document ACID Transactions & Monotonicity (Hard Rules 2 & 3) ──
    try:
        import db_queue
        med_id = ObjectId()
        now_ts = datetime.utcnow()
        tx_run_id = f"tx_{uuid.uuid4().hex[:6]}"
        
        # Setup test record in medicine_sync_state
        db.medicine_sync_state.insert_one({
            "_id": med_id,
            "shard_id": 0,
            "sync_status": "IN_PROGRESS",
            "sync_run_id": tx_run_id,
            "sync_started_at": now_ts,
            "heartbeat_at": now_ts,
            "last_synced_at": None,
            "version": 1
        })
        
        # Fresh observation with price 100
        fresh_entries = [{
            "medicine_id": med_id,
            "platform": "1mg",
            "price": 100.0,
            "sale_price": 100.0,
            "mrp": 120.0,
            "in_stock": True,
            "scraped_at": now_ts,
            "freshness_state": "Fresh",
            "updated_at": now_ts
        }]
        history_rows = [{
            "medicine_id": med_id,
            "platform": "1mg",
            "sale_price": 100.0,
            "mrp": 120.0,
            "in_stock": True,
            "scraped_at": now_ts
        }]
        
        # Commit via atomic transaction
        committed, reason = db_queue.finalize_sync_atomic(client, db, med_id, tx_run_id, fresh_entries, history_rows, duration_ms=250)
        record_test("Atomic Finalize in Transaction (Rule 2)", committed, f"Committed={committed}, Reason={reason}")
        
        # Verify fresh price stored
        stored_price_doc = db.prices.find_one({"medicine_id": med_id, "platform": "1mg"})
        fresh_price_correct = (stored_price_doc is not None) and (stored_price_doc.get("sale_price") == 100.0)
        record_test("Fresh Price Persisted", fresh_price_correct, f"sale_price={stored_price_doc.get('sale_price') if stored_price_doc else None}")
        
        # Hard Rule 3: Observation Monotonicity
        # Setup second run ownership
        second_run_id = f"tx_second_{uuid.uuid4().hex[:6]}"
        db.medicine_sync_state.update_one(
            {"_id": med_id},
            {"$set": {"sync_status": "IN_PROGRESS", "sync_run_id": second_run_id, "heartbeat_at": datetime.utcnow()}}
        )
        
        # Attempt to write an OLDER price observation (scraped 2 hours earlier) with sale_price 50.0
        older_ts = now_ts - timedelta(hours=2)
        older_entries = [{
            "medicine_id": med_id,
            "platform": "1mg",
            "price": 50.0,
            "sale_price": 50.0,
            "mrp": 120.0,
            "scraped_at": older_ts
        }]
        second_committed, second_reason = db_queue.finalize_sync_atomic(client, db, med_id, second_run_id, older_entries, [], duration_ms=100)
        
        # Check that stored price in DB is STILL 100.0 (older price 50.0 was dropped due to monotonicity)
        check_price_doc = db.prices.find_one({"medicine_id": med_id, "platform": "1mg"})
        time_diff = abs((check_price_doc.get("scraped_at") - now_ts).total_seconds())
        monotonicity_preserved = (check_price_doc.get("sale_price") == 100.0) and (time_diff < 2.0)
        record_test("Price Observation Monotonicity (Rule 3)", monotonicity_preserved,
                    f"Stored price after older write attempt={check_price_doc.get('sale_price')} (100.0 expected, 50.0 rejected)")
        
        # Ownership Lost test: third run attempts commit without lease
        lost_committed, lost_reason = db_queue.finalize_sync_atomic(client, db, med_id, "rogue_runner_id", [], [], duration_ms=50)
        ownership_rejected = (not lost_committed) and (lost_reason == "OWNERSHIP_LOST_ABORTED")
        record_test("Claim Ownership Validation", ownership_rejected, f"Unowned commit rejected={not lost_committed}, reason={lost_reason}")
        
        # Clean up test records
        db.medicine_sync_state.delete_one({"_id": med_id})
        db.prices.delete_many({"medicine_id": med_id})
        db.price_history.delete_many({"medicine_id": med_id})
    except Exception as e:
        record_test("ACID Transactions & Monotonicity", False, str(e))

    # ── Test 7: Staging Ingestion Gate & Canonical Promotion (Hard Rule 5) ──
    try:
        from pipeline.ingestion_gate import validate_staging_candidate, promote_to_canonical
        
        # Invalid candidate missing molecular salt & price
        bad_cand = {
            "raw_name": "Incomplete Syrup",
            "raw_salt": "Herbal Proprietary Blend",
            "raw_manufacturer": "Unknown",
            "raw_prices": []
        }
        is_valid_bad, reasons_bad, _ = validate_staging_candidate(bad_cand)
        bad_rejected = (not is_valid_bad) and (len(reasons_bad) >= 2)
        record_test("Ingestion Gate Rejection (Rule 5)", bad_rejected, f"Rejected invalid candidate: {reasons_bad}")
        
        # Valid candidate
        cand_id = ObjectId()
        good_cand = {
            "_id": cand_id,
            "raw_name": "Amoxicillin 500mg Capsule",
            "raw_salt": "Amoxicillin (500mg)",
            "raw_manufacturer": "Cipla Ltd",
            "raw_form": "capsule",
            "raw_prices": [{
                "platform": "apollo",
                "sale_price": 75.50,
                "mrp": 90.0,
                "url": "https://www.apollopharmacy.in/amox-500",
                "in_stock": True,
                "scraped_at": datetime.utcnow()
            }],
            "status": "APPROVED",
            "created_at": datetime.utcnow()
        }
        is_valid_good, reasons_good, cand_sig = validate_staging_candidate(good_cand)
        record_test("Ingestion Gate Validation (Rule 5)", is_valid_good, f"Resolution signature computed: {cand_sig}")
        
        db.staging_medicines.insert_one(good_cand)
        promoted, p_reason, canonical_id = promote_to_canonical(db, cand_id)
        record_test("Canonical Promotion (Rule 5)", promoted, f"Promoted={promoted}, canonical_id={canonical_id}")
        
        # Test idempotency (promoting again merges into existing canonical without creating duplicate)
        prom_again, again_reason, again_id = promote_to_canonical(db, cand_id)
        duplicate_count = db.medicines.count_documents({"resolution_signature": cand_sig})
        idempotency_ok = (prom_again is True) and (again_reason == "MERGED_EXISTING") and (again_id == canonical_id) and (duplicate_count == 1)
        record_test("Promotion Idempotency", idempotency_ok, f"Duplicate count={duplicate_count} (exactly 1 expected), reason={again_reason}")
        
        # Verify canonical medicine exists
        can_med = db.medicines.find_one({"_id": canonical_id})
        can_price = db.prices.find_one({"medicine_id": canonical_id})
        can_state = db.medicine_sync_state.find_one({"_id": canonical_id})
        
        pipeline_integrity = (can_med is not None) and (can_price is not None) and (can_state is not None)
        record_test("Canonical Pipeline Integrity", pipeline_integrity,
                    f"Canonical med={bool(can_med)}, Price={bool(can_price)}, State={bool(can_state)}")
        
        # Clean up staging & promoted test records
        if canonical_id:
            db.medicines.delete_one({"_id": canonical_id})
            db.prices.delete_many({"medicine_id": canonical_id})
            db.medicine_sync_state.delete_one({"_id": canonical_id})
        db.staging_medicines.delete_one({"_id": cand_id})
    except Exception as e:
        record_test("Staging Ingestion Gate", False, str(e))

    # ── Test 8: Scheduled Runner CLI Dry-Run (All 3 Production Modes) ──
    import subprocess
    runner_script = os.path.join(scraper_dir, "scheduled_runner.py")
    
    modes_to_test = [
        ["--mode", "hot", "--dry-run", "--limit", "3", "--no-dedup"],
        ["--mode", "shard", "--shard", "1", "--dry-run", "--limit", "3", "--no-dedup"],
        ["--mode", "discover", "--dry-run", "--limit", "3", "--no-dedup"]
    ]
    
    for args in modes_to_test:
        mode_name = args[1]
        try:
            cmd = [sys.executable, runner_script] + args
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            runner_passed = (res.returncode == 0) and ("DRY-RUN MODE ENABLED" in res.stdout or "DRY-RUN MODE ENABLED" in res.stderr)
            record_test(f"CLI scheduled_runner ({mode_name} --dry-run)", runner_passed,
                        f"exit_code={res.returncode}")
        except Exception as e:
            record_test(f"CLI scheduled_runner ({mode_name} --dry-run)", False, str(e))

    # ── Summary ──
    logger.info("=" * 75)
    logger.info("DRY-RUN EXECUTION SUMMARY")
    logger.info("=" * 75)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    
    for r in results:
        status_tag = "[PASS]" if r["passed"] else "[FAIL]"
        print(f"{status_tag} {r['name']}: {r['detail']}")
        
    print("-" * 75)
    print(f"TOTAL: {total} | PASSED: {passed} | FAILED: {failed}")
    print("=" * 75)
    
    if failed > 0:
        sys.exit(1)
    else:
        print("ALL ARCHITECTURAL COMPONENTS AND WORKFLOWS VERIFIED OPERATIONAL.")
        sys.exit(0)

if __name__ == "__main__":
    run_dry_run_suite()
