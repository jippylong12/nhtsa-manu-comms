"""Job 1: NHTSA discovery into canonical pending rows.

Polls NHTSA per tracked vehicle and inserts any communication not already in
Postgres as a `pending` canonical row, plus a `comm_documents` row per attached
PDF and a `communication_vehicles` link. It never downloads or reads a document;
that is Job 2's role, and `communications.status` is the only contract between
them.

Usage:
    python -m src.jobs.sync --all
    python -m src.jobs.sync --vehicle 218944
    python -m src.jobs.sync --import-vehicles     # bootstrap the vehicles table
    python -m src.jobs.sync --all --limit 25      # cap comms per vehicle (smoke runs)
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from dateutil import parser as dateutil_parser

from src.communications.nhtsa_client import (
    NHTSAClient,
    extract_comm_ids_from_details,
    extract_id_to_comm_number,
    extract_id_to_summary,
)
from src.communications.schemas import get_comm_type
from src.db import close_pool, get_pool
from src.jobs import repository as repo

log = logging.getLogger("sync")


@dataclass
class SyncStats:
    """Per-run counters, logged at the end of each vehicle and the whole run."""

    found: int = 0
    new_comms: int = 0
    new_docs: int = 0
    new_links: int = 0
    skipped_known: int = 0
    fetch_failures: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SyncStats") -> None:
        self.found += other.found
        self.new_comms += other.new_comms
        self.new_docs += other.new_docs
        self.new_links += other.new_links
        self.skipped_known += other.skipped_known
        self.fetch_failures += other.fetch_failures
        self.errors.extend(other.errors)

    def summary(self) -> str:
        return (
            f"found={self.found} new_comms={self.new_comms} new_docs={self.new_docs} "
            f"new_links={self.new_links} already_known={self.skipped_known} "
            f"fetch_failures={self.fetch_failures} errors={len(self.errors)}"
        )


def _parse_date(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return dateutil_parser.parse(str(raw))
    except (ValueError, TypeError, OverflowError):
        return None


def _matched_keywords(summary: Optional[str], keywords: list[str]) -> list[str]:
    if not summary or not keywords:
        return []
    text = summary.lower()
    return [k for k in keywords if k.lower() in text]


async def sync_vehicle(
    conn, client: NHTSAClient, vehicle: dict[str, Any], limit: int | None = None
) -> SyncStats:
    """Sync one vehicle. Never raises: a failure here must not abort the run."""
    stats = SyncStats()
    label = f"{vehicle['year']} {vehicle['make']} {vehicle['model']}".strip()
    nhtsa_vehicle_id = vehicle["nhtsa_vehicle_id"]

    try:
        details = await client.get_vehicle_details(nhtsa_vehicle_id)
    except Exception as e:  # noqa: BLE001 - one vehicle failing is not fatal
        msg = f"{label} ({nhtsa_vehicle_id}): details fetch failed: {type(e).__name__}: {e}"
        log.error(msg)
        stats.errors.append(msg)
        return stats

    comm_ids = extract_comm_ids_from_details(details)
    id_to_summary = extract_id_to_summary(details)
    id_to_comm_number = extract_id_to_comm_number(details)
    if limit:
        comm_ids = comm_ids[:limit]
    stats.found = len(comm_ids)
    log.info("%s (%s): %d communications listed", label, nhtsa_vehicle_id, len(comm_ids))

    # Communications already stored still need a vehicle link (a comm shared by
    # two vehicles is one row and two links), but they do not need refetching
    # from NHTSA. Skipping them is the main thing that keeps re-runs polite.
    known = await repo.existing_nhtsa_ids(conn, [str(i) for i in comm_ids])
    for nid in comm_ids:
        if str(nid) not in known:
            continue
        stats.skipped_known += 1
        row = await conn.fetchrow("SELECT id FROM communications WHERE nhtsa_id = $1", str(nid))
        if row and await repo.link_vehicle(
            conn,
            communication_id=row["id"],
            vehicle_id=vehicle["id"],
            matched_keywords=_matched_keywords(id_to_summary.get(nid), vehicle["keywords"]),
        ):
            stats.new_links += 1

    to_fetch = [i for i in comm_ids if str(i) not in known]
    if not to_fetch:
        log.info("%s: nothing new (%s)", label, stats.summary())
        return stats

    log.info("%s: fetching %d new communications", label, len(to_fetch))
    async for nhtsa_id, comm in client.fetch_communications_batch(to_fetch):
        if not comm:
            stats.fetch_failures += 1
            continue
        try:
            await _store_comm(
                conn, vehicle, nhtsa_id, comm, id_to_summary, id_to_comm_number, stats
            )
        except Exception as e:  # noqa: BLE001
            msg = f"{label}: storing {nhtsa_id} failed: {type(e).__name__}: {e}"
            log.error(msg)
            stats.errors.append(msg)

    log.info("%s: done (%s)", label, stats.summary())
    return stats


async def _store_comm(
    conn, vehicle, nhtsa_id, comm, id_to_summary, id_to_comm_number, stats
) -> None:
    """Persist one communication, its documents, and its vehicle link."""
    documents = [
        {
            "url": d.get("url", ""),
            "summary": d.get("summary", "Unknown"),
            "load_date": str(d.get("loadDate", "")),
        }
        for d in (comm.get("associatedDocuments") or [])
    ]
    summary = str(comm.get("summary", "") or "")
    # The vehicle-details payload carries the properly prefixed number
    # (PIT/PIC/PIP); the safety-issues payload often does not.
    comm_number = id_to_comm_number.get(nhtsa_id) or comm.get("manufacturerCommunicationNumber")

    async with conn.transaction():
        comm_pk, inserted = await repo.upsert_communication(
            conn,
            nhtsa_id=str(nhtsa_id),
            communication_number=comm_number,
            communication_type=get_comm_type(comm_number, summary, documents),
            communication_date=_parse_date(comm.get("communicationDate")),
            summary=summary,
            details_summary=id_to_summary.get(nhtsa_id),
            raw=json.dumps(comm, default=str),
        )
        if inserted:
            stats.new_comms += 1

        for d in documents:
            if not d["url"]:
                continue
            if await repo.upsert_document(
                conn,
                communication_id=comm_pk,
                url=d["url"],
                doc_summary=d["summary"],
                load_date=_parse_date(d["load_date"]),
            ):
                stats.new_docs += 1

        if await repo.link_vehicle(
            conn,
            communication_id=comm_pk,
            vehicle_id=vehicle["id"],
            matched_keywords=_matched_keywords(summary, vehicle["keywords"]),
        ):
            stats.new_links += 1


async def import_vehicles_from_mongo() -> int:
    """One-time bootstrap: copy tracked vehicles from the legacy Mongo store.

    Mongo records only year and model, so `make` is inferred from the model
    name where it is unambiguous and left as UNKNOWN otherwise; it is display
    metadata, not a sync key.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    from src.config import get_settings

    settings = get_settings()
    mongo = AsyncIOMotorClient(settings.mongodb_url)[settings.mongodb_database]
    vehicles = await mongo.vehicles.find({}).to_list(length=None)

    known_makes = {
        "SILVERADO": "CHEVROLET",
        "EQUINOX": "CHEVROLET",
        "BLAZER": "CHEVROLET",
        "COLORADO": "CHEVROLET",
        "SIERRA": "GMC",
        "CANYON": "GMC",
        "HUMMER": "GMC",
        "LYRIQ": "CADILLAC",
        "ESCALADE": "CADILLAC",
        "CELESTIQ": "CADILLAC",
    }

    pool = await get_pool()
    n = 0
    async with pool.acquire() as conn:
        for v in vehicles:
            model = str(v.get("model", "")).upper()
            make = next((mk for key, mk in known_makes.items() if key in model), "UNKNOWN")
            _, inserted = await repo.upsert_vehicle(
                conn,
                nhtsa_vehicle_id=int(v["vehicle_id"]),
                year=int(v["year"]),
                make=make,
                model=model,
                keywords=list(v.get("keywords") or []),
            )
            n += 1
            log.info(
                "vehicle %s %s %s (%s): %s",
                v["year"],
                make,
                model,
                v["vehicle_id"],
                "inserted" if inserted else "updated",
            )
    return n


async def run(vehicle_ids: list[int] | None, limit: int | None) -> SyncStats:
    pool = await get_pool()
    client = NHTSAClient()
    total = SyncStats()
    started = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        vehicles = await repo.tracked_vehicles(conn)
        if vehicle_ids:
            vehicles = [v for v in vehicles if v["nhtsa_vehicle_id"] in vehicle_ids]
        if not vehicles:
            log.warning(
                "No tracked vehicles matched. Run with --import-vehicles first, "
                "or check the ids passed to --vehicle."
            )
            return total

        log.info("syncing %d vehicle(s)", len(vehicles))
        for v in vehicles:
            total.merge(await sync_vehicle(conn, client, v, limit=limit))

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("RUN COMPLETE in %.1fs :: %s", elapsed, total.summary())
    for err in total.errors:
        log.error("  %s", err)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync NHTSA communications into Postgres.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="sync every active tracked vehicle")
    group.add_argument("--vehicle", type=int, action="append", help="NHTSA vehicleId (repeatable)")
    group.add_argument(
        "--import-vehicles", action="store_true", help="bootstrap the vehicles table from Mongo"
    )
    parser.add_argument("--limit", type=int, help="cap communications per vehicle (smoke runs)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    async def _main() -> int:
        try:
            if args.import_vehicles:
                n = await import_vehicles_from_mongo()
                log.info("imported %d vehicle(s)", n)
                return 0
            stats = await run(args.vehicle, args.limit)
            return 1 if stats.errors else 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
