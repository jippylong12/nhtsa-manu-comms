"""Job 1: NHTSA discovery into canonical pending rows.

Learns each tracked vehicle's communications from the `comm_products` flat-file
index -- exact match on `(model_year, make, plain nameplate)` -- and inserts any
communication not already in Postgres as a `pending` canonical row, plus a
`comm_documents` row per attached PDF and a `communication_vehicles` link. The
per-communication detail (summary, documents) still comes from NHTSA's
`/safetyIssues/byNhtsaId`, one call per new bulletin. It never downloads or reads
a document; that is Job 2's role, and `communications.status` is the only contract
between them.

The discovery step used to resolve a numeric `vehicleId` via byYmmt and read the
comm-id list out of `/vehicles/{id}/details` -- a route NHTSA's gateway hard-killed
at 30s. Since ADR 0001 (M3) the list comes from the local index instead, so
discovery cannot time out and a fully-down NHTSA only stops the per-bulletin
detail fetch, which already records its failures.

Usage:
    python -m src.jobs.sync --all
    python -m src.jobs.sync --vehicle 3
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

from src.communications.flatfile import normalize_model
from src.communications.nhtsa_client import NHTSAClient
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

    # Which communications belong to this vehicle, straight from the local index:
    # exact match on the plain nameplate NHTSA files bulletins under. `make` is
    # already uppercase; `model` is normalized the same way the index is, so a
    # vehicle keyed `CIVIC` meets `comm_products.model = 'CIVIC'`. (A vehicle still
    # keyed to a granular body style -- `F-150 REGULAR CAB` -- matches nothing
    # until the M4 backfill re-keys it to the plain nameplate; that is expected.)
    try:
        products = await conn.fetch(
            """
            SELECT nhtsa_id, summary
            FROM comm_products
            WHERE model_year = $1 AND make = $2 AND model = $3
            """,
            int(vehicle["year"]),
            str(vehicle["make"]).upper(),
            normalize_model(vehicle["model"]),
        )
    except Exception as e:  # noqa: BLE001 - one vehicle failing is not fatal
        msg = f"{label} ({vehicle['id']}): index lookup failed: {type(e).__name__}: {e}"
        log.error(msg)
        stats.errors.append(msg)
        return stats

    comm_ids = [r["nhtsa_id"] for r in products]
    # Concise summary per bulletin, from the index -- the replacement for the map
    # sync used to build from the vehicle-details payload.
    id_to_summary: dict[str, str] = {r["nhtsa_id"]: (r["summary"] or "") for r in products}
    if limit:
        comm_ids = comm_ids[:limit]
    stats.found = len(comm_ids)
    log.info("%s (%s): %d communications listed", label, vehicle["id"], len(comm_ids))

    # Communications already stored still need a vehicle link (a comm shared by
    # two vehicles is one row and two links), but they do not need refetching
    # from NHTSA. Skipping them is the main thing that keeps re-runs polite.
    known = await repo.existing_nhtsa_ids(conn, comm_ids)
    for nid in comm_ids:
        if nid not in known:
            continue
        stats.skipped_known += 1
        row = await conn.fetchrow("SELECT id FROM communications WHERE nhtsa_id = $1", nid)
        if row and await repo.link_vehicle(
            conn,
            communication_id=row["id"],
            vehicle_id=vehicle["id"],
            matched_keywords=_matched_keywords(id_to_summary.get(nid), vehicle["keywords"]),
        ):
            stats.new_links += 1

    # Convert to int only for the NHTSA call: get_safety_issue matches the
    # returned bulletin by numeric nhtsaIdNumber, so a string id would miss it.
    to_fetch = [int(nid) for nid in comm_ids if nid not in known]
    if not to_fetch:
        log.info("%s: nothing new (%s)", label, stats.summary())
        return stats

    log.info("%s: fetching %d new communications", label, len(to_fetch))
    async for nhtsa_id, comm in client.fetch_communications_batch(to_fetch):
        if not comm:
            stats.fetch_failures += 1
            continue
        try:
            await _store_comm(conn, vehicle, nhtsa_id, comm, id_to_summary, stats)
        except Exception as e:  # noqa: BLE001
            msg = f"{label}: storing {nhtsa_id} failed: {type(e).__name__}: {e}"
            log.error(msg)
            stats.errors.append(msg)

    log.info("%s: done (%s)", label, stats.summary())
    return stats


async def _store_comm(conn, vehicle, nhtsa_id, comm, id_to_summary, stats) -> None:
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
    # The comm number now comes only from the byNhtsaId payload; the vehicle-details
    # payload that once supplied the prefixed form (PIT/PIC/PIP) is gone. When it is
    # absent get_comm_type falls back to the summary and document types.
    comm_number = comm.get("manufacturerCommunicationNumber")

    async with conn.transaction():
        comm_pk, inserted = await repo.upsert_communication(
            conn,
            nhtsa_id=str(nhtsa_id),
            communication_number=comm_number,
            communication_type=get_comm_type(comm_number, summary, documents),
            communication_date=_parse_date(comm.get("communicationDate")),
            summary=summary,
            details_summary=id_to_summary.get(str(nhtsa_id)),
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


async def run(vehicle_ids: list[int] | None, limit: int | None) -> SyncStats:
    pool = await get_pool()
    client = NHTSAClient()
    total = SyncStats()
    started = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        vehicles = await repo.tracked_vehicles(conn)
        if vehicle_ids:
            vehicles = [v for v in vehicles if v["id"] in vehicle_ids]
        if not vehicles:
            log.warning(
                "No tracked vehicles matched. Add one in the app, or check the "
                "ids passed to --vehicle."
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
    group.add_argument("--vehicle", type=int, action="append", help="vehicle row id (repeatable)")
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
            stats = await run(args.vehicle, args.limit)
            return 1 if stats.errors else 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
