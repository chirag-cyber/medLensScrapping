"""
render_server.py — Flask server for deploying the scraper on Render.

Solves the Render free-tier sleep problem:
  - Render puts free web services to sleep after ~15 min of inactivity.
  - This server runs a background self-ping every 4 minutes to keep it awake.
  - APScheduler handles the daily scrape schedule (no external cron needed).

Endpoints:
  GET  /                → Health check (Render uses this to confirm the service is up)
  GET  /health          → Detailed health + last run info
  GET  /status          → Current scraper status (idle / running / last result)
  POST /run             → Manually trigger a scrape run (optional, useful for debugging)
  GET  /logs            → Last N lines from scheduler.log

Usage:
  pip install -r requirements.txt
  python render_server.py

Environment variables:
  MONGO_URL        — MongoDB connection string (required)
  RENDER_URL       — This service's own URL for self-ping (e.g. https://medlens-scraper.onrender.com)
  SCRAPE_HOUR      — Hour (UTC) to run daily scrape (default: 3)
  SCRAPE_MINUTE    — Minute to run daily scrape (default: 15)
  PORT             — Port to listen on (Render sets this automatically, default: 10000)
  DASHBOARD_KEY    — Optional key to protect /run and /logs endpoints
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import requests as http_requests
from dotenv import load_dotenv

# ── Setup ────────────────────────────────────────────────────────────────────
load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "scheduler.log")

# Ensure the scraper modules are importable
if HERE not in sys.path:
    sys.path.insert(0, HERE)

app = Flask(__name__)
logger = logging.getLogger("render_server")

# ── State tracking ───────────────────────────────────────────────────────────
_state = {
    "status": "idle",           # idle | running | failed | success
    "last_run_start": None,
    "last_run_end": None,
    "last_run_result": None,    # "success" | "failed" | "drift" | None
    "last_run_error": None,
    "total_runs": 0,
    "total_pings": 0,
    "server_start": datetime.now(timezone.utc).isoformat(),
}
_state_lock = threading.Lock()


# ── Scrape job (runs in APScheduler thread) ──────────────────────────────────

def _run_scrape_job():
    """Execute the scheduled scrape. Wraps scheduled_runner.run() logic
    but stays in-process (no subprocess) so it works on Render's free tier."""
    with _state_lock:
        if _state["status"] == "running":
            logger.warning("Scrape already running — skipping this trigger.")
            return
        _state["status"] = "running"
        _state["last_run_start"] = datetime.now(timezone.utc).isoformat()
        _state["last_run_error"] = None

    logger.info("=" * 60)
    logger.info("SCHEDULED SCRAPE START")
    logger.info("=" * 60)

    t0 = time.time()
    result = "success"
    error_msg = None

    try:
        from sync_and_scrape import ScrapeAndSync

        scraper = ScrapeAndSync()
        try:
            scraper.sync_all(update_clinical=False)
            logger.info("Sync pass finished successfully.")

            # Check for drift alarms
            drift_dead = [
                a for a in getattr(scraper, "last_drift_alarms", [])
                if a[1] == "DEAD"
            ]
            if drift_dead:
                result = "drift"
                logger.error(
                    "%d platform(s) DEAD: %s",
                    len(drift_dead),
                    ", ".join(a[0] for a in drift_dead),
                )
        finally:
            try:
                scraper.close()
            except Exception as e:
                logger.warning("scraper.close() error: %s", e)

    except Exception as e:
        result = "failed"
        error_msg = str(e)
        logger.exception("Scrape FAILED: %s", e)

    elapsed = time.time() - t0

    with _state_lock:
        _state["status"] = "idle"
        _state["last_run_end"] = datetime.now(timezone.utc).isoformat()
        _state["last_run_result"] = result
        _state["last_run_error"] = error_msg
        _state["total_runs"] += 1

    logger.info("=" * 60)
    logger.info("SCHEDULED SCRAPE END — %s in %.1fs", result.upper(), elapsed)
    logger.info("=" * 60)


# ── Self-ping keep-alive ─────────────────────────────────────────────────────

def _keep_alive_ping():
    """Ping this service's own URL to prevent Render free-tier sleep.
    Runs every 4 minutes via APScheduler."""
    render_url = os.getenv("RENDER_URL", "").rstrip("/")
    if not render_url:
        # If RENDER_URL not set, try to construct from RENDER_EXTERNAL_HOSTNAME
        hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
        if hostname:
            render_url = f"https://{hostname}"

    if not render_url:
        logger.debug("RENDER_URL not set — skipping self-ping (local dev mode).")
        return

    try:
        resp = http_requests.get(f"{render_url}/health", timeout=10)
        with _state_lock:
            _state["total_pings"] += 1
        logger.debug("Keep-alive ping: %s (status %d)", render_url, resp.status_code)
    except Exception as e:
        logger.warning("Keep-alive ping failed: %s", e)


# ── Flask routes ─────────────────────────────────────────────────────────────

def _check_auth():
    """Simple key-based auth for protected endpoints."""
    key = os.getenv("DASHBOARD_KEY", "")
    if not key:
        return True  # No key configured = open access
    provided = request.args.get("key", "") or request.headers.get("X-API-Key", "")
    return provided == key


@app.route("/")
def index():
    """Root health check — Render pings this to confirm the service is alive."""
    return jsonify({
        "service": "medlens-scraper",
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/health")
def health():
    """Detailed health status with last run info."""
    with _state_lock:
        state_copy = dict(_state)
    return jsonify({
        "service": "medlens-scraper",
        "health": "ok",
        **state_copy,
    })


@app.route("/status")
def status():
    """Current scraper state."""
    with _state_lock:
        state_copy = dict(_state)
    return jsonify(state_copy)


@app.route("/run", methods=["POST"])
@app.route("/api/scrape/sync", methods=["POST"])
def manual_run():
    """Manually trigger a sync_all scrape run. Protected by DASHBOARD_KEY.
    Accepts JSON body: { "update_clinical": bool, "reset": bool, "skip": int }"""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    with _state_lock:
        if _state["status"] == "running":
            return jsonify({
                "error": "Scrape already running",
                "started_at": _state["last_run_start"],
            }), 409

    body = request.get_json(silent=True) or {}
    update_clinical = bool(body.get("update_clinical", False))
    reset = bool(body.get("reset", False))
    skip = int(body.get("skip", 0))

    def _sync_worker():
        with _state_lock:
            _state["status"] = "running"
            _state["last_run_start"] = datetime.now(timezone.utc).isoformat()
            _state["last_run_error"] = None

        t0 = time.time()
        result = "success"
        error_msg = None
        try:
            from sync_and_scrape import ScrapeAndSync
            scraper = ScrapeAndSync()
            try:
                scraper.sync_all(skip=skip, update_clinical=update_clinical, reset=reset)
            finally:
                scraper.close()
        except Exception as e:
            result = "failed"
            error_msg = str(e)
            logger.exception("Sync run failed: %s", e)

        elapsed = time.time() - t0
        with _state_lock:
            _state["status"] = "idle"
            _state["last_run_end"] = datetime.now(timezone.utc).isoformat()
            _state["last_run_result"] = result
            _state["last_run_error"] = error_msg
            _state["total_runs"] += 1

    thread = threading.Thread(target=_sync_worker, name="api-sync", daemon=True)
    thread.start()

    return jsonify({
        "message": "Sync scrape triggered in background",
        "params": {"update_clinical": update_clinical, "reset": reset, "skip": skip},
        "check_status": "/status",
    })


@app.route("/api/scrape/medicine", methods=["POST"])
def scrape_single_medicine():
    """Scrape a single medicine by name and add/update it in MongoDB.
    Accepts JSON: { "name": "Dolo 650", "seed_urls": {"1mg": "...", ...} }"""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    name = body.get("name") or request.args.get("name")
    if not name:
        return jsonify({"error": "Missing 'name' field in request body/query"}), 400

    seed_urls = body.get("seed_urls")

    def _single_worker():
        try:
            from sync_and_scrape import ScrapeAndSync
            scraper = ScrapeAndSync()
            try:
                scraper.scrape_new(name, seed_urls=seed_urls)
                logger.info("Finished scraping single medicine: %s", name)
            finally:
                scraper.close()
        except Exception as e:
            logger.exception("Single medicine scrape failed for %s: %s", name, e)

    thread = threading.Thread(target=_single_worker, name=f"scrape-{name}", daemon=True)
    thread.start()

    return jsonify({
        "message": f"Scrape initiated for '{name}'",
        "check_logs": "/logs",
    })


@app.route("/api/dedup", methods=["POST"])
def run_dedup():
    """Run medicine deduplication & normalization.
    Accepts JSON: { "apply": true/false } (default false for dry-run report)"""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    apply_changes = bool(body.get("apply", False))

    def _dedup_worker():
        try:
            from dedup_medicines import report_and_apply
            report_and_apply(apply=apply_changes)
            logger.info("Dedup finished (apply=%s)", apply_changes)
        except Exception as e:
            logger.exception("Dedup failed: %s", e)

    thread = threading.Thread(target=_dedup_worker, name="dedup-worker", daemon=True)
    thread.start()

    return jsonify({
        "message": "Deduplication task started",
        "mode": "APPLY (writes to DB)" if apply_changes else "DRY-RUN (report only)",
        "check_logs": "/logs",
    })


@app.route("/api/llm-backfill", methods=["POST"])
def run_llm_backfill():
    """Trigger Groq LLM side effects backfill for medicines with missing/placeholder data."""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    def _llm_worker():
        try:
            from enrichment import llm_side_effects_fix
            llm_side_effects_fix.fix_side_effects()
            logger.info("LLM side-effects backfill finished.")
        except Exception as e:
            logger.exception("LLM side-effects backfill failed: %s", e)

    thread = threading.Thread(target=_llm_worker, name="llm-backfill", daemon=True)
    thread.start()

    return jsonify({
        "message": "LLM side-effects backfill started",
        "check_logs": "/logs",
    })


@app.route("/api/ensure-indexes", methods=["POST"])
def ensure_indexes():
    """Create price history and performance indexes in MongoDB."""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from sync_and_scrape import ScrapeAndSync
        scraper = ScrapeAndSync()
        try:
            idx = scraper.ensure_indexes()
            return jsonify({"message": "Indexes created successfully", "index": str(idx)})
        finally:
            scraper.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/logs")
def logs():
    """Return last N lines of scheduler.log. Protected by DASHBOARD_KEY."""
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    n = int(request.args.get("n", 100))
    n = min(n, 500)  # Cap at 500 lines

    try:
        if os.path.exists(LOG_FILE):
            from collections import deque
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                tail = deque(f, maxlen=n)
            return jsonify({
                "showing_last": n,
                "lines": [line.rstrip() for line in tail],
            })
        else:
            return jsonify({"lines": [], "note": "No log file yet."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Scheduler setup ──────────────────────────────────────────────────────────

def _create_scheduler():
    """Create and configure APScheduler with:
    1. Daily scrape job at configured time
    2. Self-ping every 4 minutes to prevent Render sleep
    """
    scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={
            "coalesce": True,        # If multiple triggers pile up, run once
            "max_instances": 1,      # Never overlap
            "misfire_grace_time": 3600,  # Allow 1 hour late start
        },
    )

    # ── Job 1: Daily scrape ──────────────────────────────────────────────
    scrape_hour = int(os.getenv("SCRAPE_HOUR", "3"))
    scrape_minute = int(os.getenv("SCRAPE_MINUTE", "15"))

    scheduler.add_job(
        _run_scrape_job,
        trigger=CronTrigger(hour=scrape_hour, minute=scrape_minute),
        id="daily_scrape",
        name=f"Daily scrape at {scrape_hour:02d}:{scrape_minute:02d} UTC",
        replace_existing=True,
    )
    logger.info(
        "Scheduled daily scrape at %02d:%02d UTC", scrape_hour, scrape_minute
    )

    # ── Job 2: Self-ping keep-alive (every 4 minutes) ────────────────────
    scheduler.add_job(
        _keep_alive_ping,
        trigger=IntervalTrigger(minutes=4),
        id="keep_alive_ping",
        name="Self-ping keep-alive (every 4 min)",
        replace_existing=True,
    )
    logger.info("Scheduled keep-alive self-ping every 4 minutes")

    return scheduler


# ── Logging setup ────────────────────────────────────────────────────────────

def _configure_logging():
    """Set up logging to stdout + rotating file."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    # Stdout (Render captures this)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    # Rotating file (persisted on Render disk)
    try:
        rotating = RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        root.addHandler(rotating)
    except Exception as e:
        logger.warning("Could not set up file logging: %s", e)


# ── Logging & Scheduler Initialization ────────────────────────────────────────

_configure_logging()
scheduler = _create_scheduler()
scheduler.start()
logger.info("APScheduler initialized with %d jobs", len(scheduler.get_jobs()))


# ── Main (Local development entry point) ─────────────────────────────────────

def main():
    port = int(os.getenv("PORT", "10000"))

    logger.info("=" * 60)
    logger.info("MedLens Scraper — Render Server starting")
    logger.info("Port: %d", port)
    logger.info("=" * 60)

    # Verify MONGO_URL is set
    if not os.getenv("MONGO_URL"):
        logger.error("MONGO_URL not set! The scraper will fail.")
        logger.error("Set it in Render's environment variables.")

    # Run Flask (Render expects us to bind to 0.0.0.0)
    try:
        app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,  # APScheduler doesn't play well with reloader
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()

