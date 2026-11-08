"""CLI: python -m src.alerts [--send] [--only-email EMAIL] [--only-user-id N] [--out-dir DIR]

Default is a dry run: it renders one HTML file per user with news and sends nothing.
Prints who would receive mail (or did), where, and the counts. No secrets are printed.
"""

import argparse
import asyncio
import logging
import pathlib
import sys
from datetime import datetime, timezone

from src.alerts.service import run_alerts
from src.db import close_pool, get_pool


async def _resolve_email(email: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM users WHERE lower(email) = lower($1)", email)


async def _main(args) -> int:
    only_user_id = args.only_user_id
    try:
        if args.only_email:
            only_user_id = await _resolve_email(args.only_email)
            if only_user_id is None:
                print(f"no user with email {args.only_email}", file=sys.stderr)
                return 2
        summary = await run_alerts(
            send=args.send,
            generated_at=datetime.now(timezone.utc),
            out_dir=pathlib.Path(args.out_dir),
            only_user_id=only_user_id,
        )
    finally:
        await close_pool()
    mode = "SEND" if args.send else "DRY RUN"
    print(f"{mode}: {len(summary.outcomes)} recipient(s)")
    for o in summary.outcomes:
        extra = f" id={o.message_id}" if o.message_id else ""
        extra += f" error={o.error}" if o.error else ""
        print(
            f"user {o.user_id} -> {o.destination} items={o.items} "
            f"ready={o.ready_vehicles} {o.outcome}{extra}"
        )
    return 1 if summary.count("failed") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-user alerts.")
    parser.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    parser.add_argument("--only-email", help="restrict the run to the user with this email")
    parser.add_argument("--only-user-id", type=int, help="restrict the run to this user id")
    parser.add_argument("--out-dir", default=".", help="where a dry run writes its HTML")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
