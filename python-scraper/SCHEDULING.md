# Scheduling the scraper

The scraper does not schedule itself. `scheduled_runner.py` is a single, safe
entrypoint an external scheduler calls; you wire the trigger. This doc has
paste-ready examples for **cron**, **systemd timers**, and **Windows Task
Scheduler**.

## What `scheduled_runner.py` does

One command that:

1. Runs a **resumable** price + clinical sync over the whole catalog
   (`sync_all()` checkpoints to `.sync-cursor.json`, so a run cut short by the
   next trigger resumes where it stopped).
2. Optionally runs **dedup** — **dry-run by default** (report only). It writes
   *only* if you pass `--dedup-apply`.
3. Holds a **pid-checked lock** (`.scheduler.lock`) so an overrunning run can't
   overlap the next trigger and double-hit the pharmacies. A stale lock left by a
   hard-killed run is detected (dead pid) and reclaimed automatically.
4. Writes a **rotating log** (`scheduler.log`, ~10 MB × 5) as well as stdout, so
   scheduled runs whose stdout is discarded still leave a trail.
5. Exits **non-zero on failure** so your scheduler's alerting fires.

### Flags

| Flag | Effect |
|------|--------|
| *(none)* | Sync pass + dedup **dry-run** report |
| `--no-dedup` | Sync pass only |
| `--dedup-apply` | Sync pass + dedup that **writes** (merges duplicates) |
| `--update-clinical` | Also re-fetch clinical fields during the sync pass |
| `--llm-backfill` | Also run the Groq LLM side-effects backfill (`openai/gpt-oss-20b`); **writes prod and costs tokens**, off by default |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | A stage failed (check `scheduler.log`) |
| `2` | Another run held the lock — this trigger was skipped (not an error) |
| `3` | Ran clean, but a platform went **DEAD** (0 hits across all its queries — selector/API drift). Investigate that scraper; the price grid is silently missing a column. |

> First-time setup (once, by you — creates a prod index, so it's gated):
> ```
> python sync_and_scrape.py --ensure-indexes
> ```
> This builds the `price_history` index. History logging works without it; the
> index just makes later trend/time-range reads fast.

---

## Prerequisites

- The `MONGO_URL` env var must be visible to the scheduled process (cron/systemd
  do **not** load your interactive shell's `.env`). Options: an `EnvironmentFile`
  (systemd), exporting it in the cron line, or a wrapper script that sources it.
- Use the **absolute path** to the same Python that has the project deps
  installed (ideally the venv interpreter, e.g. `/opt/medlens/venv/bin/python`).

Adjust these placeholders below:

- `PYTHON` → absolute path to the venv python
- `SCRAPER_DIR` → `.../medLensScrapping/python-scraper`

---

## cron (Linux/macOS)

Daily at 03:15. cron discards stdout, so the rotating `scheduler.log` is your
record; the `|| ...` line optionally surfaces a hard failure.

```cron
# m  h  dom mon dow   command
15 3 * * *  cd /path/to/SCRAPER_DIR && MONGO_URL='mongodb+srv://...' /path/to/PYTHON scheduled_runner.py >> scheduler.cron.out 2>&1
```

Prefer a wrapper so secrets aren't in the crontab:

```bash
#!/usr/bin/env bash
# run_scheduled.sh
set -euo pipefail
cd /path/to/SCRAPER_DIR
source /path/to/venv/bin/activate
set -a; source .env; set +a          # load MONGO_URL from .env
exec python scheduled_runner.py "$@"
```

```cron
15 3 * * *  /path/to/SCRAPER_DIR/run_scheduled.sh
```

To also merge duplicates on, say, Sundays only:

```cron
15 3 * * 0  /path/to/SCRAPER_DIR/run_scheduled.sh --dedup-apply
```

---

## systemd timer (Linux)

`/etc/systemd/system/medlens-scraper.service`:

```ini
[Unit]
Description=MedLens scheduled scrape (sync + dedup dry-run)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/SCRAPER_DIR
# Keep MONGO_URL out of the unit; put it in an env file root-owned 0600:
EnvironmentFile=/path/to/SCRAPER_DIR/.env
ExecStart=/path/to/PYTHON /path/to/SCRAPER_DIR/scheduled_runner.py
# Don't let a run linger past a day if it wedges:
TimeoutStartSec=6h
```

`/etc/systemd/system/medlens-scraper.timer`:

```ini
[Unit]
Description=Run MedLens scraper daily

[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true          # catch up if the box was off at trigger time
Unit=medlens-scraper.service

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now medlens-scraper.timer
systemctl list-timers medlens-scraper.timer     # confirm next run
journalctl -u medlens-scraper.service -f        # watch a run
```

> `EnvironmentFile` reads simple `KEY=value` lines. If your `.env` has quotes or
> `export`, strip them for systemd or use the cron-style wrapper instead.

---

## Windows Task Scheduler

The runner is cross-platform (the lock uses `tasklist` to probe pids on Windows).
Create a task that runs:

```
Program/script:  C:\path\to\venv\Scripts\python.exe
Add arguments:   scheduled_runner.py
Start in:        A:\medlens\Scrapping\medLensScrapping\python-scraper
```

Set `MONGO_URL` as a **system** or **user** environment variable (Task Scheduler
won't read a `.env`), or wrap in a `.cmd` that sets it first. Trigger: Daily at
03:15. Under *Settings*, tick "If the task is already running… **Do not start a
new instance**" as a belt-and-suspenders complement to the built-in lock.

---

## Recommended cadence

- **Prices** move often → a **daily** full sync is a reasonable default. If the
  catalog grows large enough that one pass exceeds the interval, the checkpoint
  makes overlapping triggers safe (the lock skips, the next run resumes).
- **Dedup** is structural and rarely needs to write → run the **dry-run daily**
  (it just reports in the log) and review before ever scheduling `--dedup-apply`,
  or apply it manually.

## Files written

| File | Purpose |
|------|---------|
| `scheduler.log` (+ `.1`…`.5`) | Rotating run log |
| `.scheduler.lock` | Overlap guard (pid inside; auto-reclaimed if stale) |
| `.sync-state-python.json` | Frozen medicine-id list for the current cycle |
| `.sync-cursor.json` | Moving checkpoint pointer (`current_index`, `completed`) |
