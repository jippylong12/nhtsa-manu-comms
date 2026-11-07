"""Copy re-read (schema v2) results from one database to another, touching nothing else.

``export`` reads the processed, summarised communications of the given vehicles from the
database in ``DATABASE_URL`` (the replica) into a JSON file. ``apply`` reads that file and
UPDATEs only the new-extraction columns of the matching rows in the database in
``DATABASE_URL`` (production). It spends no model money and replaces re-reading.

Safety, by construction:
  * the only write statements are the two UPDATEs below (no INSERT, DELETE or TRUNCATE),
    and they name ``communications`` and ``comm_documents`` only;
  * only ``COMM_COLUMNS`` and ``DOC_COLUMNS`` are ever set;
  * a communication is matched by ``nhtsa_id`` and a document by ``(communication_id, url)``;
    a record missing at the destination is skipped and listed, never inserted;
  * every UPDATE runs in one transaction, rolled back on any error;
  * ``apply`` is a dry run unless ``--apply`` is given, and with skips it still rolls back
    unless ``--allow-skips`` is also given.

Usage:
    python -m src.scripts.copy_schema_v2 export --vehicle 6 --vehicle 7 --out FILE.json
    python -m src.scripts.copy_schema_v2 apply --in FILE.json            # dry run
    python -m src.scripts.copy_schema_v2 apply --in FILE.json --apply [--allow-skips]
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import asyncpg

from src.db import close_pool, get_pool

log = logging.getLogger("copy_schema_v2")

SCHEMA_VERSION = 2

COMM_COLUMNS = ("status", "status_reason", "processed_at", "severity", "severity_source")
DOC_COLUMNS = (
    "llm_summary",
    "doc_kind",
    "symptoms",
    "systems",
    "components",
    "remedy",
    "applicability",
    "applicability_years",
    "applicability_models",
    "applicability_conditions",
    "audience",
    "llm_model",
    "llm_tokens_in",
    "llm_tokens_out",
    "llm_cost_usd",
    "llm_at",
    "embedding",
    "embedding_model",
    "embedded_at",
)
_TIMESTAMPS = {"processed_at", "llm_at", "embedded_at"}

# (table, column) pairs the destination must have (migrations 0010 to 0012).
REQUIRED_COLUMNS = (
    ("comm_documents", "applicability_years"),
    ("comm_documents", "audience"),
    ("communications", "severity"),
)

# The select list: the embedding travels as pgvector text.
_DOC_SELECT = ", ".join("embedding::text AS embedding" if c == "embedding" else c for c in DOC_COLUMNS)

_UPDATE_COMM = (
    "UPDATE communications SET "
    + ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(COMM_COLUMNS))
    + " WHERE id = $1"
)
_UPDATE_DOC = (
    "UPDATE comm_documents SET "
    + ", ".join(
        f"{c} = ${i + 2}::vector" if c == "embedding" else f"{c} = ${i + 2}"
        for i, c in enumerate(DOC_COLUMNS)
    )
    + " WHERE id = $1"
)


class PreconditionError(Exception):
    """The destination cannot take this file (exit code 2)."""


class _Rollback(Exception):
    """Raised inside the transaction to roll it back without it being an error."""


# --- (de)serialisation ------------------------------------------------------------------


def _to_json(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in _TIMESTAMPS:
        return value.isoformat()
    if col == "llm_cost_usd":
        return str(value)
    return value  # text, ints, arrays and the embedding text pass through


def _from_json(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in _TIMESTAMPS:
        return datetime.fromisoformat(value)
    if col == "llm_cost_usd":
        return Decimal(value)
    return value


# --- export -----------------------------------------------------------------------------


async def export_records(conn: asyncpg.Connection, vehicles: list[int]) -> dict[str, Any]:
    """Read-only: the payload for ``vehicles`` (header plus one record per communication)."""
    comms = await conn.fetch(
        f"""
        SELECT c.id, c.nhtsa_id, {", ".join("c." + c for c in COMM_COLUMNS)}
        FROM communications c
        WHERE c.status = 'processed'
          AND EXISTS (SELECT 1 FROM communication_vehicles cv
                      WHERE cv.communication_id = c.id AND cv.vehicle_id = ANY($1::bigint[]))
          AND EXISTS (SELECT 1 FROM comm_documents d
                      WHERE d.communication_id = c.id AND d.llm_summary IS NOT NULL)
        ORDER BY c.nhtsa_id
        """,
        vehicles,
    )
    records = []
    doc_count = 0
    for c in comms:
        docs = await conn.fetch(
            f"SELECT url, {_DOC_SELECT} FROM comm_documents "
            "WHERE communication_id = $1 AND llm_summary IS NOT NULL ORDER BY id",
            c["id"],
        )
        record: dict[str, Any] = {"nhtsa_id": c["nhtsa_id"]}
        for col in COMM_COLUMNS:
            record[col] = _to_json(col, c[col])
        record["documents"] = [
            {"url": d["url"], **{col: _to_json(col, d[col]) for col in DOC_COLUMNS}} for d in docs
        ]
        doc_count += len(docs)
        records.append(record)
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "vehicles": sorted(vehicles),
        "source_counts": {"communications": len(records), "documents": doc_count},
        "communications": records,
    }


# --- apply ------------------------------------------------------------------------------


async def check_destination(conn: asyncpg.Connection) -> list[str]:
    """The required v2 columns the destination lacks, as ``table.column`` strings."""
    rows = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = ANY($1::text[]) AND column_name = ANY($2::text[])",
        sorted({t for t, _ in REQUIRED_COLUMNS}),
        sorted({c for _, c in REQUIRED_COLUMNS}),
    )
    present = {(r["table_name"], r["column_name"]) for r in rows}
    return [f"{t}.{c}" for t, c in REQUIRED_COLUMNS if (t, c) not in present]


def validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or "communications" not in payload:
        raise PreconditionError(
            f"file is not a schema v{SCHEMA_VERSION} export (schema_version="
            f"{payload.get('schema_version')!r})"
        )


class Report:
    """What an apply did or would do."""

    def __init__(self) -> None:
        self.communications_updated = 0
        self.documents_updated = 0
        self.communications_identical = 0
        self.documents_identical = 0
        self.skipped: list[str] = []

    def lines(self) -> list[str]:
        out = [
            f"communications to update/updated: {self.communications_updated}",
            f"documents to update/updated:      {self.documents_updated}",
            f"communications already identical: {self.communications_identical}",
            f"documents already identical:      {self.documents_identical}",
            f"skipped (missing at destination): {len(self.skipped)}",
        ]
        out += [f"  - {s}" for s in self.skipped]
        return out


async def _plan_and_write(
    conn: asyncpg.Connection, payload: dict[str, Any], write: bool
) -> Report:
    report = Report()
    for rec in payload["communications"]:
        nid = rec["nhtsa_id"]
        row = await conn.fetchrow(
            f"SELECT id, {', '.join(COMM_COLUMNS)} FROM communications WHERE nhtsa_id = $1", nid
        )
        if row is None:
            report.skipped.append(f"communication {nid}")
            continue
        comm_id = row["id"]
        # A document the destination has and the export does not (sync attached a new PDF
        # there after the clone) would be left unread while its communication is forced to
        # `processed`, and the LLM stage only reads `pending`. Skip the whole communication.
        dest_urls = {
            r["url"]
            for r in await conn.fetch(
                "SELECT url FROM comm_documents WHERE communication_id = $1", comm_id
            )
        }
        extra = sorted(dest_urls - {d["url"] for d in rec["documents"]})
        if extra:
            report.skipped.append(
                f"communication {nid} has {len(extra)} document(s) not in the export: "
                + ", ".join(extra)
            )
            continue
        wanted = [_from_json(c, rec[c]) for c in COMM_COLUMNS]
        if all(row[c] == w for c, w in zip(COMM_COLUMNS, wanted)):
            report.communications_identical += 1
        else:
            report.communications_updated += 1
            if write:
                await conn.execute(_UPDATE_COMM, comm_id, *wanted)
        for doc in rec["documents"]:
            drow = await conn.fetchrow(
                f"SELECT id, {_DOC_SELECT} FROM comm_documents "
                "WHERE communication_id = $1 AND url = $2",
                comm_id,
                doc["url"],
            )
            if drow is None:
                report.skipped.append(f"document {nid} {doc['url']}")
                continue
            dwanted = [_from_json(c, doc[c]) for c in DOC_COLUMNS]
            if all(drow[c] == w for c, w in zip(DOC_COLUMNS, dwanted)):
                report.documents_identical += 1
            else:
                report.documents_updated += 1
                if write:
                    await conn.execute(_UPDATE_DOC, drow["id"], *dwanted)
    return report


async def apply_records(
    conn: asyncpg.Connection,
    payload: dict[str, Any],
    *,
    apply: bool = False,
    allow_skips: bool = False,
) -> tuple[int, Report]:
    """Return ``(exit_code, report)``. Writes only when ``apply``; all in one transaction.

    Exit 0: nothing skipped. Exit 1: something was skipped (and, unless ``allow_skips`` and
    ``apply``, nothing was written). Preconditions raise ``PreconditionError`` (exit 2).
    """
    validate_payload(payload)
    missing = await check_destination(conn)
    if missing:
        raise PreconditionError(
            "destination lacks schema v2 columns: "
            + ", ".join(missing)
            + " (run `python -m src.db.migrate` there first)"
        )
    report = Report()
    try:
        async with conn.transaction():
            report = await _plan_and_write(conn, payload, write=apply)
            if report.skipped and not (apply and allow_skips):
                raise _Rollback()
    except _Rollback:
        pass
    return (1 if report.skipped else 0), report


# --- CLI --------------------------------------------------------------------------------


async def _cmd_export(args: argparse.Namespace) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        payload = await export_records(conn, args.vehicle)
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1)
    counts = payload["source_counts"]
    print(
        f"exported {counts['communications']} communications, {counts['documents']} documents "
        f"for vehicles {payload['vehicles']} to {args.out}"
    )
    return 0


async def _cmd_apply(args: argparse.Namespace) -> int:
    with open(args.infile) as fh:
        payload = json.load(fh)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            code, report = await apply_records(
                conn, payload, apply=args.apply, allow_skips=args.allow_skips
            )
        except PreconditionError as exc:
            print(f"PRECONDITION FAILED: {exc}", file=sys.stderr)
            return 2
    mode = "APPLY" if args.apply else "DRY RUN (nothing written; add --apply to write)"
    print(f"mode: {mode}")
    if args.apply:
        print("Reminder: take a backup first: pg_dump -t communications -t comm_documents")
    print("\n".join(report.lines()))
    if report.skipped and not (args.apply and args.allow_skips):
        print("Skips found: nothing was written. Fix the mismatch or pass --apply --allow-skips.")
    elif args.apply:
        print("committed.")
    return code


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Copy schema v2 re-read results between databases.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="read-only export from DATABASE_URL")
    exp.add_argument("--vehicle", type=int, action="append", required=True)
    exp.add_argument("--out", required=True)
    app = sub.add_parser("apply", help="update DATABASE_URL from an export (dry run by default)")
    app.add_argument("--in", dest="infile", required=True)
    app.add_argument("--apply", action="store_true", help="actually write")
    app.add_argument("--allow-skips", action="store_true", help="commit matched rows despite skips")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )

    async def _main() -> int:
        try:
            return await (_cmd_export(args) if args.cmd == "export" else _cmd_apply(args))
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
