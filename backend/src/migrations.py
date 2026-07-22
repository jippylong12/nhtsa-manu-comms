"""One-off maintenance over the canonical store.

`communication_type` is derived by `get_comm_type` when sync inserts a row, so
rows written before a change to those rules keep the old answer. This re-derives
them in place.

The other backfill that lived here is gone. `backfill_comm_counts` recounted a
`comm_count` column on the Mongo vehicle document, and since JIP-416 the count
is a correlated subquery over `communication_vehicles` evaluated on every read.
There is no stored copy left to drift, so there is nothing to backfill: the bug
it existed to repair cannot recur.

Usage:
    python -m src.migrations communication_types
    python -m src.migrations communication_types --dry-run
"""

import argparse
import asyncio
import json
import sys

import asyncpg

from src.communications.schemas import get_comm_type
from src.config import get_settings


async def backfill_communication_types(dry_run: bool = False) -> int:
    """Re-derive communication_type for every stored communication.

    The documents the classifier looks at are the `associatedDocuments` of the
    raw NHTSA payload, which is kept verbatim in the `raw` column precisely so
    derived fields can be recomputed without refetching.
    """
    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set. See docs/database.md.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT id, communication_number, communication_type,
                   coalesce(summary, details_summary, '') AS summary, raw
            FROM communications
            """
        )
        changed = 0
        for row in rows:
            raw = row["raw"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            documents = (raw or {}).get("associatedDocuments") or []
            new_type = get_comm_type(row["communication_number"], row["summary"], documents)
            if new_type == row["communication_type"]:
                continue
            changed += 1
            print(
                f"  {row['communication_number'] or row['id']}: "
                f"{row['communication_type']} -> {new_type}"
            )
            if not dry_run:
                await conn.execute(
                    "UPDATE communications SET communication_type = $2 WHERE id = $1",
                    row["id"],
                    new_type,
                )

        verb = "would change" if dry_run else "changed"
        print(f"{verb} {changed} of {len(rows)} communication(s).")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["communication_types"])
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    return asyncio.run(backfill_communication_types(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
