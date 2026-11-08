"""End-to-end pipeline runner: sync -> processor -> alerts, in one command.

Intended to be run by hand (`python -m src.jobs.run_pipeline`), not by a
daemon. A launchd plist is provided in `deploy/` and documented in the runbook
for anyone who later wants it unattended, but nothing here installs or requires
it.

Safety:
- A lockfile prevents two runs from overlapping (a second run exits immediately
  rather than double-processing).
- Each run writes a timestamped log file; logs older than the retention window
  are pruned.
- The status column makes the whole thing recoverable: a run killed mid-way
  leaves rows in `pending`/`failed`, and the next run drains them. Nothing here
  needs to clean up after a crash.

Usage:
    python -m src.jobs.run_pipeline                 # sync + process (dry-run alerts)
    python -m src.jobs.run_pipeline --send-digest   # also send the alert emails (alias: --send-alerts)
    python -m src.jobs.run_pipeline --sync-only
    python -m src.jobs.run_pipeline --skip-sync     # process + alerts only (skips the scan too)
    python -m src.jobs.run_pipeline --skip-scan     # sync without scanning NHTSA's bulletin files first
"""

import argparse
import asyncio
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Optional

from src.alerts import service as alerts
from src.communications import flatfile
from src.db import close_pool, get_pool
from src.jobs import extract as extract_stage
from src.jobs import process as processor
from src.jobs import sync as sync_job

log = logging.getLogger("pipeline")

LOG_RETENTION_DAYS = 30


# Hidden files under the backend working directory, matching the runbook,
# the reference plist, and .gitignore.
def _log_dir() -> pathlib.Path:
    d = pathlib.Path(".pipeline_logs")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path() -> pathlib.Path:
    return pathlib.Path(".pipeline.lock")


class _Lock:
    """A simple PID lockfile. Refuses to run if a live process already holds it."""

    def __init__(self, path: pathlib.Path):
        self.path = path

    def __enter__(self):
        # Atomic create-or-fail (O_EXCL) so two runs starting close together
        # cannot both pass a check-then-write and proceed. If it already exists,
        # inspect the holder and only remove it if the process is truly gone.
        try:
            self._acquire()
        except FileExistsError:
            pid = self._read_pid()
            if pid and _pid_alive(pid):
                raise SystemExit(
                    f"another pipeline run is active (pid {pid}, lock {self.path}). Exiting."
                )
            log.warning("removing stale lockfile from pid %s", pid)
            self.path.unlink(missing_ok=True)
            self._acquire()
        return self

    def _acquire(self) -> None:
        # "x" opens for exclusive creation, failing atomically if it exists.
        with open(self.path, "x") as fh:
            fh.write(str(os.getpid()))

    def _read_pid(self) -> Optional[int]:
        try:
            return int(self.path.read_text().strip())
        except (ValueError, OSError):
            return None

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prune_logs(retention_days: int = LOG_RETENTION_DAYS, now: datetime | None = None) -> int:
    """Delete run logs older than the retention window. Returns count removed."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - retention_days * 86400
    removed = 0
    for f in _log_dir().glob("run-*.log"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def _configure_logging(run_id: str) -> pathlib.Path:
    """Log to both stdout and a per-run file."""
    log_file = _log_dir() / f"run-{run_id}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_file


async def _run(args, now: datetime) -> int:
    failures: list[str] = []

    if not args.skip_sync and not args.skip_scan:
        log.info("=== STAGE: scan ===")
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                report = await flatfile.refresh_index(conn)
            for window, result in report.items():
                if result.get("status") == "error":
                    # Sync still runs: it uses whatever the ledger already holds.
                    log.error("scan: %s: %s", window, result.get("error"))
                    failures.append(f"scan: {window}: {result.get('error')}")
        except Exception as e:  # noqa: BLE001
            log.exception("scan stage failed")
            failures.append(f"scan: {type(e).__name__}: {e}")

    if not args.skip_sync:
        log.info("=== STAGE: sync ===")
        try:
            stats = await sync_job.run(vehicle_ids=None, limit=None)
            if stats.errors:
                failures.append(f"sync: {len(stats.errors)} error(s)")
        except Exception as e:  # noqa: BLE001
            log.exception("sync stage failed")
            failures.append(f"sync: {type(e).__name__}: {e}")

    if not args.sync_only:
        log.info("=== STAGE: process (extract -> llm -> embed) ===")
        try:
            await processor.run_extract(None)
            await processor.run_llm(None, sync=args.sync_llm)
            await processor.run_embed(None)
        except Exception as e:  # noqa: BLE001
            log.exception("processor stage failed")
            failures.append(f"process: {type(e).__name__}: {e}")

        if not args.keep_pdfs:
            # The archive is a download cache with no expiry, so an unattended
            # run would fill the disk one bulletin at a time. Only documents
            # that already have a summary are dropped; see prune_archive.
            log.info("=== STAGE: prune pdf archive ===")
            try:
                removed, freed = await extract_stage.prune_archive()
                log.info("pruned %d pdf(s), freed %.1f MB", removed, freed / 1_048_576)
            except Exception as e:  # noqa: BLE001
                # Reclaiming disk is housekeeping; never fail a run over it.
                log.warning("archive prune skipped: %s: %s", type(e).__name__, e)

        log.info("=== STAGE: alerts ===")
        try:
            await alerts.run_alerts(send=args.send_digest, generated_at=now)
        except Exception as e:  # noqa: BLE001
            log.exception("alerts stage failed")
            failures.append(f"alerts: {type(e).__name__}: {e}")

    status = await processor.status()
    log.info("pipeline status: %s", status)

    if failures:
        # A non-zero exit is the failure signal a wrapper (or a human checking
        # the last run) keys on. The runbook documents this.
        log.error("RUN COMPLETED WITH FAILURES: %s", "; ".join(failures))
        return 1
    log.info("RUN COMPLETED CLEANLY")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full NHTSA Comms pipeline once.")
    parser.add_argument("--sync-only", action="store_true", help="run sync, skip processing")
    parser.add_argument(
        "--skip-sync", action="store_true", help="skip scan and sync, process + alerts"
    )
    parser.add_argument(
        "--skip-scan", action="store_true", help="skip the bulletin-file scan before sync"
    )
    parser.add_argument(
        "--keep-pdfs",
        action="store_true",
        help="do not prune archived PDFs for already-summarised documents",
    )
    parser.add_argument(
        "--send-digest",
        "--send-alerts",
        dest="send_digest",
        action="store_true",
        help="send the alert emails (default: dry run)",
    )
    parser.add_argument("--sync-llm", action="store_true", help="use the synchronous Messages API instead of the Batch API")
    return parser


def main() -> int:
    args = _parser().parse_args()

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    log_file = _configure_logging(run_id)
    log.info("pipeline run %s starting; logging to %s", run_id, log_file)
    pruned = _prune_logs(now=now)
    if pruned:
        log.info("pruned %d log(s) older than %d days", pruned, LOG_RETENTION_DAYS)

    async def _main() -> int:
        try:
            return await _run(args, now)
        finally:
            await close_pool()

    try:
        with _Lock(_lock_path()):
            return asyncio.run(_main())
    except SystemExit as e:
        # Lock contention path.
        log.error(str(e))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
