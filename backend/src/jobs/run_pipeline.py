"""End-to-end pipeline runner: sync -> processor -> digest, in one command.

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
    python -m src.jobs.run_pipeline                 # sync + process (dry-run digest)
    python -m src.jobs.run_pipeline --send-digest   # also send the digest email
    python -m src.jobs.run_pipeline --sync-only
    python -m src.jobs.run_pipeline --skip-sync     # process + digest only
"""

import argparse
import asyncio
import logging
import os
import pathlib
from datetime import datetime, timezone

from src.db import close_pool
from src.jobs import digest as digest_job
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
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
            except (ValueError, OSError):
                pid = None
            if pid and _pid_alive(pid):
                raise SystemExit(
                    f"another pipeline run is active (pid {pid}, lock {self.path}). Exiting."
                )
            log.warning("removing stale lockfile from pid %s", pid)
            self.path.unlink(missing_ok=True)
        self.path.write_text(str(os.getpid()))
        return self

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

        log.info("=== STAGE: digest ===")
        try:
            await digest_job.run_digest(send=args.send_digest, generated_at=now)
        except Exception as e:  # noqa: BLE001
            log.exception("digest stage failed")
            failures.append(f"digest: {type(e).__name__}: {e}")

    status = await processor.status()
    log.info("pipeline status: %s", status)

    if failures:
        # A non-zero exit is the failure signal a wrapper (or a human checking
        # the last run) keys on. The runbook documents this.
        log.error("RUN COMPLETED WITH FAILURES: %s", "; ".join(failures))
        return 1
    log.info("RUN COMPLETED CLEANLY")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full NHTSA Comms pipeline once.")
    parser.add_argument("--sync-only", action="store_true", help="run sync, skip processing")
    parser.add_argument("--skip-sync", action="store_true", help="skip sync, process + digest")
    parser.add_argument(
        "--send-digest", action="store_true", help="send the digest (default: dry run)"
    )
    parser.add_argument("--sync-llm", action="store_true", help="use the synchronous LLM API")
    args = parser.parse_args()

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
