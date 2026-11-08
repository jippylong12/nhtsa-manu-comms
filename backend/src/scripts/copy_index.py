"""Copy the lean flat-file index from one database to another, instead of rebuilding it there.

Rebuilding the index on production streams 3.9 million flat-file rows through the Railway
proxy and timed out on the large windows. The replica already holds the finished result
(about 30 MB), so ``export`` writes it to a JSON file and ``apply`` loads it into the
destination in one transaction: `flatfile_ingests`, `comm_vocab`, `vehicle_scans` and
`vehicle_bulletins` are replaced wholesale (they are derived caches), and the tracked
vehicles whose ids exist at the destination get their identity (year, make, model, keywords,
active) from the source, which is the plain-nameplate re-key. Nothing else is touched;
vehicles missing at the destination (test cars on the replica) are skipped and listed.

Usage:
    DATABASE_URL="$TEST_DATABASE_URL" python -m src.scripts.copy_index export --out FILE.json
    python -m src.scripts.copy_index apply --in FILE.json            # dry run
    python -m src.scripts.copy_index apply --in FILE.json --apply
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from src.db import close_pool, get_pool

log = logging.getLogger("copy_index")

SCHEMA_VERSION = 1
REQUIRED_TABLES = ("comm_vocab", "vehicle_bulletins", "vehicle_scans", "flatfile_ingests")
VEHICLE_COLUMNS = ("id", "year", "make", "model", "keywords", "active")


class PreconditionError(Exception):
    """The destination cannot take this file (exit code 2)."""


def _ts(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def export_index(conn: asyncpg.Connection) -> dict[str, Any]:
    """Read-only: every index table plus the tracked vehicles' identities."""
    ingests = await conn.fetch(
        "SELECT source_window, etag, last_modified, row_count, ingested_at "
        "FROM flatfile_ingests ORDER BY source_window"
    )
    vocab = await conn.fetch(
        "SELECT model_year, make, model, source_window, bulletins FROM comm_vocab "
        "ORDER BY source_window, model_year, make, model"
    )
    scans = await conn.fetch(
        "SELECT vehicle_id, source_window, etag, scanned_at FROM vehicle_scans "
        "ORDER BY vehicle_id, source_window"
    )
    bulletins = await conn.fetch(
        "SELECT vehicle_id, nhtsa_id, summary, source_window FROM vehicle_bulletins "
        "ORDER BY vehicle_id, nhtsa_id"
    )
    vehicles = await conn.fetch(
        f"SELECT {', '.join(VEHICLE_COLUMNS)} FROM vehicles ORDER BY id"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "flatfile_ingests": [{k: _ts(v) for k, v in dict(r).items()} for r in ingests],
        "comm_vocab": [list(r) for r in vocab],
        "vehicle_scans": [{k: _ts(v) for k, v in dict(r).items()} for r in scans],
        "vehicle_bulletins": [list(r) for r in bulletins],
        "vehicles": [dict(r) for r in vehicles],
    }


async def check_destination(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = ANY($1::text[])",
        list(REQUIRED_TABLES),
    )
    present = {r["table_name"] for r in rows}
    return [t for t in REQUIRED_TABLES if t not in present]


class Report:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.vehicles_updated = 0
        self.skipped_vehicles: list[str] = []
        self.dropped_rows = 0

    def lines(self) -> list[str]:
        out = [f"{t}: {n} rows" for t, n in self.counts.items()]
        out.append(f"vehicles updated from the source identity: {self.vehicles_updated}")
        out.append(f"vehicles skipped (missing at destination): {len(self.skipped_vehicles)}")
        out += [f"  - {s}" for s in self.skipped_vehicles]
        if self.dropped_rows:
            out.append(f"scan/bulletin rows dropped with those vehicles: {self.dropped_rows}")
        return out


async def apply_index(
    conn: asyncpg.Connection, payload: dict[str, Any], *, apply: bool = False
) -> Report:
    """Replace the four index tables and refresh the shared vehicles, in one transaction.

    A dry run does everything inside the transaction and rolls it back, so the counts it
    prints are the counts the real run writes.
    """
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PreconditionError(f"not a copy_index export (schema_version={payload.get('schema_version')!r})")
    missing = await check_destination(conn)
    if missing:
        raise PreconditionError(
            "destination lacks the lean index tables: "
            + ", ".join(missing)
            + " (run `python -m src.db.migrate` there first)"
        )
    report = Report()
    dest_ids = {
        r["id"] for r in await conn.fetch("SELECT id FROM vehicles WHERE id = ANY($1::bigint[])",
                                          [v["id"] for v in payload["vehicles"]])
    }
    keep = [v for v in payload["vehicles"] if v["id"] in dest_ids]
    report.skipped_vehicles = [
        f"vehicle {v['id']} ({v['year']} {v['make']} {v['model']})"
        for v in payload["vehicles"]
        if v["id"] not in dest_ids
    ]
    scans = [s for s in payload["vehicle_scans"] if s["vehicle_id"] in dest_ids]
    bulletins = [b for b in payload["vehicle_bulletins"] if b[0] in dest_ids]
    report.dropped_rows = (len(payload["vehicle_scans"]) - len(scans)) + (
        len(payload["vehicle_bulletins"]) - len(bulletins)
    )

    tr = conn.transaction()
    await tr.start()
    try:
        for v in keep:
            await conn.execute(
                "UPDATE vehicles SET year = $2, make = $3, model = $4, keywords = $5, active = $6 "
                "WHERE id = $1",
                v["id"], v["year"], v["make"], v["model"], list(v["keywords"] or []), v["active"],
            )
        report.vehicles_updated = len(keep)
        await conn.execute("TRUNCATE flatfile_ingests, comm_vocab, vehicle_scans, vehicle_bulletins")
        await conn.copy_records_to_table(
            "flatfile_ingests",
            records=[
                (r["source_window"], r["etag"], _dt(r["last_modified"]), r["row_count"],
                 _dt(r["ingested_at"]))
                for r in payload["flatfile_ingests"]
            ],
            columns=["source_window", "etag", "last_modified", "row_count", "ingested_at"],
        )
        await conn.copy_records_to_table(
            "comm_vocab",
            records=[tuple(r) for r in payload["comm_vocab"]],
            columns=["model_year", "make", "model", "source_window", "bulletins"],
        )
        await conn.copy_records_to_table(
            "vehicle_scans",
            records=[(s["vehicle_id"], s["source_window"], s["etag"], _dt(s["scanned_at"]))
                     for s in scans],
            columns=["vehicle_id", "source_window", "etag", "scanned_at"],
        )
        await conn.copy_records_to_table(
            "vehicle_bulletins",
            records=[tuple(b) for b in bulletins],
            columns=["vehicle_id", "nhtsa_id", "summary", "source_window"],
        )
        for t in REQUIRED_TABLES:
            report.counts[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
        if apply:
            await tr.commit()
        else:
            await tr.rollback()
    except Exception:
        await tr.rollback()
        raise
    return report


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


async def _cmd_export(args: argparse.Namespace) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        payload = await export_index(conn)
    with open(args.out, "w") as fh:
        json.dump(payload, fh)
    print(
        f"exported {len(payload['comm_vocab'])} vocab rows, {len(payload['vehicle_bulletins'])} "
        f"bulletin rows, {len(payload['vehicle_scans'])} scan rows, "
        f"{len(payload['flatfile_ingests'])} windows, {len(payload['vehicles'])} vehicles to {args.out}"
    )
    return 0


async def _cmd_apply(args: argparse.Namespace) -> int:
    with open(args.infile) as fh:
        payload = json.load(fh)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            report = await apply_index(conn, payload, apply=args.apply)
        except PreconditionError as exc:
            print(f"PRECONDITION FAILED: {exc}", file=sys.stderr)
            return 2
    print("mode: " + ("APPLY" if args.apply else "DRY RUN (nothing written; add --apply to write)"))
    print("\n".join(report.lines()))
    if args.apply:
        print("committed.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Copy the lean flat-file index between databases.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="read-only export from DATABASE_URL")
    exp.add_argument("--out", required=True)
    app = sub.add_parser("apply", help="replace the index in DATABASE_URL from an export")
    app.add_argument("--in", dest="infile", required=True)
    app.add_argument("--apply", action="store_true", help="actually write")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    async def _main() -> int:
        try:
            return await (_cmd_export(args) if args.cmd == "export" else _cmd_apply(args))
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
