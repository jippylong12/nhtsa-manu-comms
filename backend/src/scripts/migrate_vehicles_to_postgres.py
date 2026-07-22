"""One-time move of tracked vehicles from Mongo onto Postgres (JIP-416).

Three jobs, all idempotent, all safe to re-run:

1. Import any Mongo `vehicles` document with no Postgres row, preserving its
   original created_at rather than stamping today.
2. Carry `last_fetched` across. Migration 0004 seeds the new column from the
   newest communication link, which is only a lower bound and is null for a
   vehicle whose corpus never got built. The Mongo document knows the real
   answer.
3. Backfill `make` for rows reading 'UNKNOWN'. Those came from
   `import_vehicles_from_mongo`, which guessed the make from a hardcoded map of
   GM nameplates, so anything else became UNKNOWN. The `vehicle_catalog` cache
   already holds the true make and trim per vehicleId; NHTSA byYmmt is the
   fallback for a vehicle that was never cached.

Usage:
    python -m src.scripts.migrate_vehicles_to_postgres            # migrate, report
    python -m src.scripts.migrate_vehicles_to_postgres --drop     # then drop the collection

`--drop` refuses unless every Mongo vehicle is present and active in Postgres,
so the collection can only go once its contents demonstrably survived.
"""

import argparse
import asyncio
import sys
from typing import Any, Optional

import asyncpg

from src.config import get_settings
from src.database import close_mongodb_connection, connect_to_mongodb, get_database


async def _catalog_identity(vehicle_id: int) -> Optional[tuple[str, Optional[str]]]:
    """Return (make, trim) for a vehicleId from the catalog cache, or None."""
    doc = await get_database().vehicle_catalog.find_one({"vehicle_id": vehicle_id})
    if not doc or not doc.get("make"):
        return None
    return str(doc["make"]).upper(), doc.get("trim")


async def _nhtsa_identity(year: int, model: str, vehicle_id: int) -> Optional[tuple[str, None]]:
    """Ask NHTSA which make owns a vehicleId. Only reached on a catalog miss."""
    from src.communications.nhtsa_client import NHTSAClient

    client = NHTSAClient()
    try:
        details = await client.get_vehicle_details(vehicle_id)
    except Exception as e:  # noqa: BLE001 - a backfill must not abort on one lookup
        print(f"  ! NHTSA lookup failed for {vehicle_id}: {type(e).__name__}: {e}")
        return None

    for result in (details or {}).get("results", []):
        for product in result.get("associatedProducts", []) or []:
            if str(product.get("productModel", "")).upper() == model.upper():
                make = product.get("productMake")
                if make:
                    return str(make).upper(), None
    return None


async def import_missing(conn: asyncpg.Connection, docs: list[dict[str, Any]]) -> int:
    """Insert Mongo vehicles that never reached Postgres."""
    imported = 0
    for doc in docs:
        vid = doc["vehicle_id"]
        if await conn.fetchval("SELECT 1 FROM vehicles WHERE nhtsa_vehicle_id = $1", vid):
            continue
        await conn.execute(
            """
            INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model, keywords,
                                  created_at, updated_at, last_fetched_at)
            VALUES ($1, $2, 'UNKNOWN', $3, $4, $5, $5, $6)
            """,
            vid,
            int(doc["year"]),
            str(doc["model"]).upper(),
            doc.get("keywords") or [],
            doc.get("created_at"),
            doc.get("last_fetched"),
        )
        print(f"  + imported {vid} {doc['year']} {doc['model']}")
        imported += 1
    return imported


async def carry_last_fetched(conn: asyncpg.Connection, docs: list[dict[str, Any]]) -> int:
    """Adopt the Mongo last_fetched wherever it is later than what we have."""
    updated = 0
    for doc in docs:
        fetched = doc.get("last_fetched")
        if not fetched:
            continue
        row = await conn.fetchrow(
            """
            UPDATE vehicles SET last_fetched_at = $2
            WHERE nhtsa_vehicle_id = $1
              AND (last_fetched_at IS NULL OR last_fetched_at < $2)
            RETURNING nhtsa_vehicle_id
            """,
            doc["vehicle_id"],
            fetched,
        )
        if row:
            print(f"  ~ last_fetched {doc['vehicle_id']} -> {fetched:%Y-%m-%d %H:%M}")
            updated += 1
    return updated


async def backfill_makes(conn: asyncpg.Connection) -> int:
    """Replace the 'UNKNOWN' make placeholder with the real one."""
    rows = await conn.fetch(
        "SELECT nhtsa_vehicle_id, year, model FROM vehicles WHERE make = 'UNKNOWN'"
    )
    fixed = 0
    for row in rows:
        vid = row["nhtsa_vehicle_id"]
        identity = await _catalog_identity(vid) or await _nhtsa_identity(
            row["year"], row["model"], vid
        )
        if identity is None:
            print(f"  ? no make found for {vid} {row['year']} {row['model']}, left UNKNOWN")
            continue
        make, trim = identity
        await conn.execute(
            "UPDATE vehicles SET make = $2, trim = coalesce(trim, $3) "
            "WHERE nhtsa_vehicle_id = $1",
            vid,
            make,
            trim,
        )
        print(f"  ~ make {vid} {row['year']} {row['model']} -> {make}")
        fixed += 1
    return fixed


async def verify(conn: asyncpg.Connection, docs: list[dict[str, Any]]) -> list[int]:
    """Return the vehicle ids that are not tracked in Postgres."""
    missing = []
    for doc in docs:
        ok = await conn.fetchval(
            "SELECT active FROM vehicles WHERE nhtsa_vehicle_id = $1", doc["vehicle_id"]
        )
        if not ok:
            missing.append(doc["vehicle_id"])
    return missing


async def run(drop: bool) -> int:
    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set. See docs/database.md.", file=sys.stderr)
        return 2

    await connect_to_mongodb()
    conn = await asyncpg.connect(settings.database_url)
    try:
        docs = [d async for d in get_database().vehicles.find()]
        print(f"Mongo vehicles: {len(docs)}")

        imported = await import_missing(conn, docs)
        carried = await carry_last_fetched(conn, docs)
        makes = await backfill_makes(conn)
        print(f"imported {imported}, last_fetched carried {carried}, makes fixed {makes}")

        missing = await verify(conn, docs)
        if missing:
            print(f"NOT tracked in Postgres: {missing}", file=sys.stderr)
            if drop:
                print("refusing to drop the collection", file=sys.stderr)
            return 1

        print("every Mongo vehicle is tracked in Postgres")
        if drop:
            await get_database().vehicles.drop()
            print("dropped Mongo collection 'vehicles'")
        return 0
    finally:
        await conn.close()
        await close_mongodb_connection()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop",
        action="store_true",
        help="drop the Mongo vehicles collection once every row is verified present",
    )
    return asyncio.run(run(parser.parse_args().drop))


if __name__ == "__main__":
    raise SystemExit(main())
