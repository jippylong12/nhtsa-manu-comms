"""Email digest entry point, now a thin shim over `src.alerts`.

The single-recipient digest became per-user alerts (src/alerts/service.py). This
module keeps the pipeline stage and the `python -m src.jobs.digest` CLI working by
running the alert pass for the admin user only. Sending is off by default.

Usage:
    python -m src.jobs.digest                 # dry run: render HTML to disk
    python -m src.jobs.digest --send          # actually send + advance the watermark
    python -m src.jobs.digest --since-days 30 # override watermark for a test render
"""

import argparse
import asyncio
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.alerts import service as alerts
from src.db import close_pool, get_pool

log = logging.getLogger("digest")


async def run_digest(
    send: bool,
    generated_at: datetime,
    since_override: Optional[datetime] = None,
    out_dir: Optional[pathlib.Path] = None,
) -> alerts.AlertRunSummary:
    """Run the alert pass for the (lowest-id) admin user. No admin yet: nothing to do."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        admin_id = await conn.fetchval(
            "SELECT id FROM users WHERE is_admin ORDER BY id LIMIT 1"
        )
    if admin_id is None:
        log.info("no admin user yet; nothing to send")
        return alerts.AlertRunSummary()
    return await alerts.run_alerts(
        send=send,
        generated_at=generated_at,
        out_dir=out_dir,
        only_user_id=admin_id,
        since_override=since_override,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Email digest of new communications.")
    parser.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    parser.add_argument(
        "--since-days", type=int, help="ignore the watermark; include the last N days"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --since-days is a test-render knob; combined with --send it would email an
    # arbitrary window as "new" and rewrite the real watermark. Refuse the combination.
    if args.since_days is not None and args.send:
        parser.error("--since-days is for dry-run rendering only; do not combine it with --send")

    async def _main() -> int:
        now = datetime.now(timezone.utc)
        since_override = (
            now - timedelta(days=args.since_days) if args.since_days is not None else None
        )
        try:
            await run_digest(send=args.send, generated_at=now, since_override=since_override)
            return 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
