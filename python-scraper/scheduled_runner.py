"""
scheduled_runner.py — single entrypoint for an *unattended*, scheduled scrape run.

The scraper had no scheduler and no run-to-run log (audit H2 / Dim 15): every
refresh was a human typing `python sync_and_scrape.py --sync-all`. This wraps that
into one command an external scheduler (cron / systemd timer / Task Scheduler) can
call safely and repeatedly:

  * ONE resumable price+clinical sync pass over the whole catalog
    (sync_all() already checkpoints via .sync-cursor.json, so a run killed by the
    next trigger picks up where it left off).
  * OPTIONAL dedup — DRY-RUN by default (report only). Dedup mutates prod data, so
    it stays user-gated: pass --dedup-apply to actually merge. Without it you get
    the report in the log and nothing is written.
  * A pid-checked lock file (.scheduler.lock) so an overrun run cannot overlap the
    next scheduled trigger and double-hit the pharmacies.
  * A rotating file log (scheduler.log, ~10MB x5) IN ADDITION to stdout — the
    scheduled path previously logged to stdout only, which cron discards.
  * A non-zero exit code on any failure, so the scheduler's own alerting fires.

This file is the SCRIPT only. Wiring the actual trigger (cron line / systemd unit /
Task Scheduler task) is left to the operator — see SCHEDULING.md for paste-ready
examples.

Usage:
    python scheduled_runner.py                 # sync pass + dedup DRY-RUN report
    python scheduled_runner.py --no-dedup      # sync pass only
    python scheduled_runner.py --dedup-apply   # sync pass + dedup that WRITES (gated)
    python scheduled_runner.py --update-clinical   # also re-fetch clinical fields
    python scheduled_runner.py --llm-backfill  # also LLM-fill placeholder side_effects (gated)

Exit codes:
    0  success
    1  a stage raised / failed
    2  another run holds the lock (skipped this trigger)
    3  a platform went DEAD (0 hits across all its queries — selector/API drift)
"""

import os
import sys
import time
import errno
import logging
import argparse
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(HERE, ".scheduler.lock")
LOG_FILE = os.path.join(HERE, "scheduler.log")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOCKED = 2
EXIT_DRIFT = 3

logger = logging.getLogger("scheduled_runner")


def _configure_logging():
    """stdout (for interactive / journald capture) + a rotating file (for cron,
    which throws stdout away). ~10MB x 5 keeps a bounded on-disk history."""
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    rotating = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    logger.addHandler(rotating)

    # Also route the scraper modules' own loggers through these handlers so the
    # scheduled log captures the full run, not just this wrapper's lines.
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (stream, rotating):
        root.addHandler(h)


def _pid_alive(pid: int) -> bool:
    """True if a process with `pid` currently exists (best-effort, cross-platform)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # No os.kill(0) semantics on Windows; use tasklist as a portable probe.
        import subprocess
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out
        except Exception:
            # If we cannot tell, assume alive — safer to skip than to double-run.
            return True
    else:
        try:
            os.kill(pid, 0)
        except OSError as e:
            return e.errno != errno.ESRCH  # ESRCH => no such process
        return True


def acquire_lock() -> bool:
    """Create .scheduler.lock atomically. If it already exists, honor it only when
    the recorded pid is still alive — otherwise treat it as stale (previous run was
    hard-killed) and take it over. Returns True on acquire, False if a live run holds it."""
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        # Someone holds it — is that someone still alive?
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                other = int((f.read().strip() or "-1"))
        except Exception:
            other = -1

        if _pid_alive(other):
            logger.warning(
                "Another run (pid %s) holds %s — skipping this trigger.",
                other, LOCK_FILE)
            return False

        logger.warning(
            "Stale lock from dead pid %s — reclaiming %s.", other, LOCK_FILE)
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
        # One retry; if this races another starter, give up rather than double-run.
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            logger.warning("Lock re-taken by another starter — skipping.")
            return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except OSError as e:
        logger.warning("Could not remove lock file %s: %s", LOCK_FILE, e)


def run(update_clinical: bool, do_dedup: bool, dedup_apply: bool, do_llm_backfill: bool,
        mode: str = "legacy", shard: int = 0, total_shards: int = 4, limit: int = None, run_id: str = None,
        dry_run: bool = False) -> int:
    """Run the sync pass, then optional dedup. Returns a process exit code."""
    # Imported lazily so --help / lock-skip do not pay the MongoClient connect.
    from sync_and_scrape import ScrapeAndSync
    import uuid

    t0 = time.time()
    failed = False
    drift_dead = []

    logger.info("=" * 70)
    logger.info("Scheduled run START (mode=%s, shard=%d/%d, update_clinical=%s, dry_run=%s)",
                mode, shard, total_shards, update_clinical, dry_run)
    logger.info("=" * 70)

    # ── Stage 1: price + clinical sync (resumable or queue-driven) ──
    scraper = ScrapeAndSync()
    try:
        if dry_run:
            logger.info("DRY-RUN MODE ENABLED: Simulating queue operations without scraper execution.")
            if mode == "hot":
                import db_queue
                db_queue.initialize_sync_states_if_needed(scraper.db, total_shards=total_shards)
                actual_limit = limit or 5
                actual_run_id = run_id or f"dryrun_hot_{uuid.uuid4().hex[:8]}"
                claimed = db_queue.claim_hot_batch_atomic(scraper.db, run_id=actual_run_id, batch_size=actual_limit)
                logger.info("[DRY-RUN] Claimed %d hot items: %s", len(claimed), claimed[:5])
                if claimed:
                    scraper.db.medicine_sync_state.update_many(
                        {"_id": {"$in": claimed}},
                        {"$set": {"sync_status": "READY", "sync_run_id": None}}
                    )
                    logger.info("[DRY-RUN] Released %d test claims back to READY.", len(claimed))

            elif mode == "shard":
                import db_queue
                db_queue.initialize_sync_states_if_needed(scraper.db, total_shards=total_shards)
                actual_limit = limit or 5
                actual_run_id = run_id or f"dryrun_shard{shard}_{uuid.uuid4().hex[:8]}"
                claimed = db_queue.claim_batch_atomic(scraper.db, shard_id=shard, run_id=actual_run_id, batch_size=actual_limit)
                logger.info("[DRY-RUN] Claimed %d shard %d items: %s", len(claimed), shard, claimed[:5])
                if claimed:
                    scraper.db.medicine_sync_state.update_many(
                        {"_id": {"$in": claimed}},
                        {"$set": {"sync_status": "READY", "sync_run_id": None}}
                    )
                    logger.info("[DRY-RUN] Released %d test claims back to READY.", len(claimed))

            elif mode == "discover":
                actual_limit = limit or 10
                misses = list(scraper.db.search_misses.find(
                    {"status": "PENDING_DISCOVERY", "intent": "QUALIFIED_MEDICINE"}
                ).sort([("hit_count", -1), ("last_seen_at", -1)]).limit(actual_limit))
                logger.info("[DRY-RUN] Found %d qualified search misses ready for discovery: %s",
                            len(misses), [m.get("raw_query") for m in misses[:5]])
            else:
                logger.info("[DRY-RUN] Legacy mode checked: medicines in DB = %d", scraper.medicines_col.count_documents({}))
            logger.info("[DRY-RUN] Simulation successful. No external network requests executed.")

        elif mode == "hot":
            import db_queue
            db_queue.initialize_sync_states_if_needed(scraper.db, total_shards=total_shards)
            actual_limit = limit or 250
            actual_run_id = run_id or f"hot_{uuid.uuid4().hex[:8]}"
            logger.info("Claiming top hot/stale batch (limit=%d, run_id=%s)...", actual_limit, actual_run_id)
            claimed = db_queue.claim_hot_batch_atomic(scraper.db, run_id=actual_run_id, batch_size=actual_limit)
            logger.info("Claimed %d hot medicines. Beginning sync...", len(claimed))
            stats = scraper.sync_claimed_batch(claimed, run_id=actual_run_id, update_clinical=update_clinical)
            logger.info("Hot sync finished: %s", stats)

        elif mode == "shard":
            import db_queue
            db_queue.initialize_sync_states_if_needed(scraper.db, total_shards=total_shards)
            actual_limit = limit or 400
            actual_run_id = run_id or f"shard{shard}_{uuid.uuid4().hex[:8]}"
            logger.info("Claiming shard %d batch (limit=%d, run_id=%s)...", shard, actual_limit, actual_run_id)
            claimed = db_queue.claim_batch_atomic(scraper.db, shard_id=shard, run_id=actual_run_id, batch_size=actual_limit)
            logger.info("Claimed %d shard medicines. Beginning sync...", len(claimed))
            stats = scraper.sync_claimed_batch(claimed, run_id=actual_run_id, update_clinical=update_clinical)
            logger.info("Shard sync finished: %s", stats)

        elif mode == "discover":
            from pipeline.ingestion_gate import promote_to_canonical
            actual_limit = limit or 50
            logger.info("Querying qualified search misses (limit=%d)...", actual_limit)
            misses = list(scraper.db.search_misses.find(
                {"status": "PENDING_DISCOVERY", "intent": "QUALIFIED_MEDICINE"}
            ).sort([("hit_count", -1), ("last_seen_at", -1)]).limit(actual_limit))
            logger.info("Discovered %d pending qualified misses to scrape.", len(misses))
            
            for miss in misses:
                q = miss["raw_query"]
                try:
                    logger.info("Discovery scraping query: '%s'", q)
                    scraper.scrape_new(q)
                    scraper.db.search_misses.update_one(
                        {"_id": miss["_id"]},
                        {"$set": {"status": "SCRAPED"}}
                    )
                except Exception as e:
                    logger.error("Failed to scrape discovered miss '%s': %s", q, e)

        else:
            # Legacy whole-catalog file-checkpointed pass
            scraper.sync_all(update_clinical=update_clinical)
            logger.info("Sync pass finished.")

        drift_dead = [a for a in getattr(scraper, "last_drift_alarms", []) if a[1] == "DEAD"]
        if drift_dead:
            logger.error("%d platform(s) DEAD this run: %s", len(drift_dead), ", ".join(a[0] for a in drift_dead))
    except Exception as e:
        failed = True
        logger.exception("Sync pass FAILED: %s", e)
    finally:
        try:
            scraper.close()
        except Exception as e:
            logger.warning("scraper.close() error (ignoring): %s", e)

    # ── Stage 2: dedup (dry-run unless explicitly applied) ──
    if do_dedup and mode in ("legacy", "shard"):
        try:
            from dedup_medicines import report_and_apply
            mode_str = "APPLY (writes)" if dedup_apply else "DRY-RUN (report only)"
            logger.info("Running dedup: %s", mode_str)
            report_and_apply(apply=dedup_apply)
            logger.info("Dedup finished.")
        except Exception as e:
            failed = True
            logger.exception("Dedup FAILED: %s", e)

    # ── Stage 3: LLM side-effects backfill (opt-in, writes) ──
    if do_llm_backfill:
        try:
            logger.info("Running LLM side-effects backfill (openai/gpt-oss-20b)...")
            from enrichment import llm_side_effects_fix
            llm_side_effects_fix.fix_side_effects()
            logger.info("Side-effects backfill finished.")
        except Exception as e:
            failed = True
            logger.exception("Side-effects backfill FAILED: %s", e)

    elapsed = time.time() - t0
    logger.info("=" * 70)
    status = "FAILED" if failed else ("OK-BUT-DRIFT" if drift_dead else "OK")
    logger.info("Scheduled run END — %s in %.1fs", status, elapsed)
    logger.info("=" * 70)
    if failed:
        return EXIT_FAIL
    if drift_dead:
        return EXIT_DRIFT
    return EXIT_OK


def main():
    ap = argparse.ArgumentParser(
        description="Unattended scheduled scraper run (sync + optional dedup).")
    ap.add_argument("--mode", choices=["legacy", "hot", "shard", "discover"], default="legacy",
                    help="Execution mode: legacy (full catalog), hot (trending), shard (matrix), discover (search misses)")
    ap.add_argument("--shard", type=int, default=0, help="Shard index (0..total_shards-1) for shard mode")
    ap.add_argument("--total-shards", type=int, default=4, help="Total number of parallel shards")
    ap.add_argument("--limit", type=int, default=None, help="Maximum medicines to process in this run")
    ap.add_argument("--run-id", type=str, default=None, help="Explicit runner UUID/Identifier")
    ap.add_argument("--update-clinical", action="store_true",
                    help="Also force re-fetch of clinical fields during the sync pass.")
    ap.add_argument("--no-dedup", action="store_true",
                    help="Skip the dedup stage entirely.")
    ap.add_argument("--dedup-apply", action="store_true",
                    help="Let the dedup stage WRITE (merge duplicates). Off by default.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Simulate queue claim and runner flow without invoking external scrapers or mutating price records.")
    ap.add_argument("--llm-backfill", action="store_true",
                    help="Also run the Groq LLM side-effects backfill.")
    args = ap.parse_args()

    _configure_logging()

    if not acquire_lock():
        sys.exit(EXIT_LOCKED)

    try:
        code = run(
            update_clinical=args.update_clinical,
            do_dedup=not args.no_dedup,
            dedup_apply=args.dedup_apply,
            do_llm_backfill=args.llm_backfill,
            mode=args.mode,
            shard=args.shard,
            total_shards=args.total_shards,
            limit=args.limit,
            run_id=args.run_id,
            dry_run=args.dry_run,
        )
    except Exception as e:
        logger.exception("Unhandled error in scheduled run: %s", e)
        code = EXIT_FAIL
    finally:
        release_lock()

    sys.exit(code)


if __name__ == "__main__":
    main()
