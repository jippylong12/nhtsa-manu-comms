"""One-time copy of the YMM cache from Mongo into Postgres (JIP-420).

The catalog is fully re-derivable from NHTSA, so dropping and refilling it would
also work. Copying is better: the rows are already correct, and a cold cache
means the add-vehicle form pays a byYmmt round trip per model selection until it
warms up again, which JIP-410 measured at 560ms against 3ms.

Idempotent. Safe to re-run.

Usage:
    python -m src.scripts.migrate_vehicle_catalog
    python -m src.scripts.migrate_vehicle_catalog --dry-run
"""

import argparse
import asyncio
import sys

import asyncpg

from src.config import get_settings
from src.database import close_mongodb_connection, connect_to_mongodb, get_database


async def run(dry_run: bool) -> int:
    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set. See docs/database.md.", file=sys.stderr)
        return 2

    await connect_to_mongodb()
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = []
        skipped = 0
        async for doc in get_database().vehicle_catalog.find():
            vehicle_id = doc.get("vehicle_id")
            year = doc.get("year")
            make = doc.get("make")
            model = doc.get("model")
            # A row missing any of these cannot serve the only lookup the table
            # has, so carrying it over would just be litter.
            if not vehicle_id or year is None or not make or not model:
                skipped += 1
                continue
            rows.append(
                (
                    int(vehicle_id),
                    doc.get("ncap_id"),
                    int(year),
                    str(make).upper(),
                    str(model).upper(),
                    doc.get("trim"),
                    doc.get("series"),
                )
            )

        print(f"Mongo vehicle_catalog: {len(rows)} usable row(s), {skipped} skipped")
        if dry_run:
            print("dry run, nothing written")
            return 0

        # Deduplicate on the primary key first: Mongo's unique index was on
        # vehicle_id too, but a warm-started catalog predates it, and
        # executemany would abort the whole batch on the first collision.
        deduped = {r[0]: r for r in rows}
        await conn.executemany(
            """
            INSERT INTO vehicle_catalog
                (vehicle_id, ncap_id, year, make, model, trim, series)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (vehicle_id) DO UPDATE SET
                ncap_id = EXCLUDED.ncap_id,
                year    = EXCLUDED.year,
                make    = EXCLUDED.make,
                model   = EXCLUDED.model,
                trim    = EXCLUDED.trim,
                series  = EXCLUDED.series
            """,
            list(deduped.values()),
        )

        total = await conn.fetchval("SELECT count(*) FROM vehicle_catalog")
        print(f"Postgres vehicle_catalog now holds {total} row(s)")
        return 0
    finally:
        await conn.close()
        await close_mongodb_connection()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    return asyncio.run(run(parser.parse_args().dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
