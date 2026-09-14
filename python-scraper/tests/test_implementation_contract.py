"""
MedLens Scraper — Implementation Contract Acceptance Tests
==========================================================
Verifies all 9 acceptance test cases from the Implementation Contract:
1. Concurrency Race & Claim Ownership Guard
2. Demand Smoothing & Floor Guard
3. Idempotent Promotion & Molecular Salt Protection
4. Analytics Graceful Flush on Shutdown
5. Search Miss Non-Blocking Queue Ingestion
6. Production Transactions Required Policy
7. Price Observation Monotonicity Guard
8. Shutdown Flush Bounded Retries & No Silent Drop
9. Canonical Promotion Gate Required Field Validation
"""

import os
import sys
import unittest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from bson.objectid import ObjectId

# Add parent dir and medSave-India backend dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
medsave_backend = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "medSave-India", "backend"))
if medsave_backend not in sys.path:
    sys.path.append(medsave_backend)

from analytics_queue import AnalyticsBatchDrainer

from db_queue import (
    compute_hot_priority_score,
    finalize_sync_atomic,
    _write_prices_internal
)
from medicine_identity import (
    compute_resolution_signature,
    _salt_is_molecular,
    extract_formulation_modifier
)
from pipeline.ingestion_gate import (
    validate_staging_candidate,
    promote_to_canonical
)


class TestImplementationContract(unittest.TestCase):

    # ── Test 1: Concurrency Race & Claim Ownership Guard ──
    def test_claim_ownership_rejection(self):
        """When another runner claims or heartbeat expires, late runner is aborted."""
        mock_client = MagicMock()
        mock_client.is_primary = True  # Replica set active
        mock_session = MagicMock()
        mock_client.start_session.return_value.__enter__.return_value = mock_session
        
        mock_db = MagicMock()
        # update_one on medicine_sync_state with sync_run_id="run_A" returns matched_count=0 (claimed by B)
        update_res = MagicMock()
        update_res.matched_count = 0
        mock_db.medicine_sync_state.with_options.return_value.update_one.return_value = update_res
        
        med_id = ObjectId()
        success, reason = finalize_sync_atomic(
            mock_client, mock_db, med_id, run_id="run_A",
            price_entries=[{"platform": "1mg", "sale_price": 30.0, "mrp": 35.0, "scraped_at": datetime.utcnow()}],
            history_rows=[], duration_ms=100
        )
        
        self.assertFalse(success)
        self.assertEqual(reason, "OWNERSHIP_LOST_ABORTED")
        mock_session.abort_transaction.assert_called_once()
        # Ensure zero price writes occurred
        mock_db.prices.with_options.return_value.update_one.assert_not_called()

    # ── Test 2: Demand Smoothing & Floor Guard ──
    def test_demand_smoothing_and_floor(self):
        # Case 1: Low volume (D_7d = 2, D_prior = 0) -> Floor forces trend = 1.0 -> score = 2.0
        score_1 = compute_hot_priority_score(demand_7d=2, demand_prior_7d=0, staleness_hours=0)
        self.assertEqual(score_1, 2.0)

        # Case 2: Surging volume (D_7d = 200, D_prior = 20) -> (200+10)/(20+10) = 7.0 -> Capped at 2.5
        # Score = 200 * 2.5 = 500.0
        score_2 = compute_hot_priority_score(demand_7d=200, demand_prior_7d=20, staleness_hours=0)
        self.assertEqual(score_2, 500.0)

        # Case 3: Collapsing volume (D_7d = 15, D_prior = 100) -> (15+10)/(100+10) = 25/110 ≈ 0.22 -> Floored at 0.5
        # Score = 15 * 0.5 = 7.5
        score_3 = compute_hot_priority_score(demand_7d=15, demand_prior_7d=100, staleness_hours=0)
        self.assertEqual(score_3, 7.5)

    # ── Test 3: Idempotent Promotion & Molecular Salt Protection ──
    def test_idempotent_promotion_molecular_salt_protection(self):
        mock_db = MagicMock()
        staging_id = ObjectId()
        canonical_id = ObjectId()
        
        # Candidate with raw prose composition
        candidate = {
            "_id": staging_id,
            "raw_name": "Dolo 650 Tablet",
            "raw_salt": "Paracetamol (650mg)",
            "raw_manufacturer": "Micro Labs Ltd",
            "raw_form": "tablet",
            "source_platform": "1mg",
            "source_url": "https://www.1mg.com/dolo",
            "raw_prices": [{"mrp": 35.0, "sale_price": 30.0, "pack_size": "15 tablets", "in_stock": True}]
        }
        mock_db.staging_medicines.find_one.return_value = candidate
        
        # Existing canonical record already has clean verified molecular salt
        mock_db.medicines.find_one.return_value = {
            "_id": canonical_id,
            "name": "Dolo 650 Tablet",
            "salt": "Paracetamol (650mg)",
            "manufacturer": "Micro Labs Ltd"
        }
        
        ok, msg, res_id = promote_to_canonical(mock_db, staging_id)
        self.assertTrue(ok)
        self.assertEqual(msg, "MERGED_EXISTING")
        self.assertEqual(res_id, canonical_id)
        # Canonical medicines collection was NOT inserted into (idempotent, no duplicates)
        mock_db.medicines.with_options.return_value.insert_one.assert_not_called()
        # Staging status marked APPROVED
        mock_db.staging_medicines.update_one.assert_called_with(
            {"_id": staging_id},
            {
                "$set": {
                    "status": "APPROVED",
                    "resolution_signature": unittest.mock.ANY,
                    "matched_canonical_id": canonical_id,
                    "promoted_at": unittest.mock.ANY
                }
            }
        )

    # ── Test 4: Analytics Graceful Flush on Shutdown ──
    def test_analytics_flush_on_shutdown(self):
        mock_db = MagicMock()
        drainer = AnalyticsBatchDrainer(db_getter=lambda: mock_db, shutdown_timeout_sec=2.0)
        
        for i in range(50):
            drainer.record_search_event(f"query_{i}", f"query_{i}", 1, [ObjectId()])
            
        drainer.flush_and_stop()
        self.assertTrue(drainer.q.empty())
        mock_db.search_events.with_options.return_value.insert_many.assert_called()

    # ── Test 5: Search Miss Non-Blocking (Hard Rule 1) ──
    def test_search_miss_non_blocking_buffer(self):
        mock_db = MagicMock()
        drainer = AnalyticsBatchDrainer(db_getter=lambda: mock_db)
        
        t0 = time.time()
        drainer.record_search_miss("some unknown brand 500mg", "someunknownbrand500mg")
        elapsed = time.time() - t0
        
        # Immediate return in < 50ms
        self.assertLess(elapsed, 0.05)
        self.assertFalse(drainer.q.empty())
        # DB search_misses was NOT directly touched in caller thread
        mock_db.search_misses.update_one.assert_not_called()

    # ── Test 6: Production Transactions Required (Hard Rule 2) ──
    def test_production_transactions_required(self):
        mock_client = MagicMock()
        mock_client.is_primary = False
        mock_client.is_mongos = False  # Standalone
        mock_db = MagicMock()
        
        ok, reason = finalize_sync_atomic(
            mock_client, mock_db, ObjectId(), "run_123",
            price_entries=[], history_rows=[], duration_ms=100
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "TRANSACTIONS_REQUIRED")

    # ── Test 7: Price Observation Monotonicity (Hard Rule 3) ──
    def test_price_observation_monotonicity(self):
        mock_db = MagicMock()
        med_id = ObjectId()
        stored_scraped_at = datetime.utcnow()
        older_scraped_at = stored_scraped_at - timedelta(hours=1)
        
        # Stored observation exists with stored_scraped_at
        mock_db.prices.find_one.return_value = {"scraped_at": stored_scraped_at}
        
        # Incoming observation is older
        incoming_price = {
            "platform": "1mg",
            "sale_price": 25.0,
            "mrp": 30.0,
            "scraped_at": older_scraped_at
        }
        
        _write_prices_internal(mock_db, med_id, "run_123", [incoming_price], [], datetime.utcnow())
        # Stale observation must be rejected (update_one not called)
        mock_db.prices.with_options.return_value.update_one.assert_not_called()

    # ── Test 8: Shutdown Flush Bounded Retries (Hard Rule 4) ──
    def test_shutdown_flush_retries_without_silent_drop(self):
        mock_db = MagicMock()
        # Fail first 2 times, then succeed
        call_count = 0
        def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Transient DB connection error")
            return MagicMock()
            
        mock_db.search_events.with_options.return_value.insert_many.side_effect = fail_then_succeed
        drainer = AnalyticsBatchDrainer(db_getter=lambda: mock_db, shutdown_timeout_sec=2.0)
        
        drainer.record_search_event("test", "test", 1, [ObjectId()])
        drainer.flush_and_stop()
        
        self.assertTrue(drainer.q.empty())
        self.assertGreaterEqual(call_count, 3)

    # ── Test 9: Canonical Promotion Gate Required Fields (Hard Rule 5) ──
    def test_canonical_promotion_gate_validation(self):
        # Case A: Missing manufacturer
        cand_a = {
            "raw_name": "Paracip 500 Tablet",
            "raw_salt": "Paracetamol (500mg)",
            "raw_manufacturer": "",  # Empty
            "raw_form": "tablet",
            "raw_prices": [{"mrp": 20.0, "sale_price": 18.0}]
        }
        valid, reasons, _ = validate_staging_candidate(cand_a)
        self.assertFalse(valid)
        self.assertTrue(any("manufacturer" in r for r in reasons))

        # Case B: Prose / Non-molecular salt
        cand_b = {
            "raw_name": "Herbal Cold Relief Tablet",
            "raw_salt": "Each tablet contains mixture of pure extracts and vitamins",
            "raw_manufacturer": "Herbal Pharma",
            "raw_form": "tablet",
            "raw_prices": [{"mrp": 50.0, "sale_price": 45.0}]
        }
        valid, reasons, _ = validate_staging_candidate(cand_b)
        self.assertFalse(valid)
        self.assertTrue(any("INVALID_MOLECULAR_SALT" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
