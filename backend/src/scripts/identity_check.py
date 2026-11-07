"""Identity/superset check: no tracked vehicle lost communications (JIP-435).

Read-only. Per active vehicle it reports linked communications against what the
flat-file index lists for the vehicle's identity and, with ``--baseline``, against
the counts recorded before the plain-nameplate migration.

Exit 1 when any of these hold, otherwise 0:
  * a ``communication_vehicles`` row points at no vehicle (orphan),
  * two active vehicles share one ``(year, make, model)`` identity,
  * a vehicle's linked count is below its baseline,
  * a vehicle in the baseline is no longer an active vehicle (its links cannot be
    shown to have survived).

Usage:
    python -m src.scripts.identity_check
    python -m src.scripts.identity_check --baseline tests/fixtures/identity_baseline_2026-07-24.json
    python -m src.scripts.identity_check --json
"""

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import asyncpg

from src.communications.flatfile import model_aliases
from src.db import close_pool, get_pool

log = logging.getLogger("identity_check")


@dataclass
class VehicleRow:
    """One active vehicle's counts and verdict."""

    id: int
    identity: str
    linked: int
    index: int
    baseline: Optional[int]
    delta: Optional[int]
    status: str  # ok | BELOW BASELINE


@dataclass
class Report:
    """The whole check: per-vehicle rows plus the two global integrity checks."""

    rows: list[VehicleRow]
    orphans: int
    duplicates: list[tuple[int, str, str, list[int]]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # baseline ids with no active vehicle

    @property
    def exit_code(self) -> int:
        below = any(r.status != "ok" for r in self.rows)
        return 1 if (self.orphans or self.duplicates or self.missing or below) else 0


def load_baseline(path: str) -> dict[str, int]:
    """A JSON map of ``{"<vehicle id>": <linked count>}``."""
    with open(path) as fh:
        return {str(k): int(v) for k, v in json.load(fh).items()}


def build_report(
    vehicles: list[dict[str, Any]], orphans: int, baseline: Optional[dict[str, int]]
) -> Report:
    """Pure: turn fetched rows into the report and its verdicts."""
    rows: list[VehicleRow] = []
    groups: dict[tuple[int, str, str], list[int]] = {}
    for v in vehicles:
        groups.setdefault((v["year"], v["make"], v["model"]), []).append(v["id"])
        base = baseline.get(str(v["id"])) if baseline is not None else None
        below = base is not None and v["linked"] < base
        rows.append(
            VehicleRow(
                id=v["id"],
                identity=f"{v['year']} {v['make']} {v['model']}",
                linked=v["linked"],
                index=v["index"],
                baseline=base,
                delta=None if base is None else v["linked"] - base,
                status="BELOW BASELINE" if below else "ok",
            )
        )
    duplicates = [(*k, ids) for k, ids in groups.items() if len(ids) > 1]
    active_ids = {str(v["id"]) for v in vehicles}
    missing = sorted(
        (k for k in (baseline or {}) if k not in active_ids), key=lambda k: (len(k), k)
    )
    return Report(rows=rows, orphans=orphans, duplicates=duplicates, missing=missing)


async def collect(conn: asyncpg.Connection) -> tuple[list[dict[str, Any]], int]:
    """Read the active vehicles with their counts, and the orphan count."""
    vehicles = await conn.fetch(
        "SELECT v.id, v.year, v.make, v.model, "
        "(SELECT count(DISTINCT cv.communication_id) FROM communication_vehicles cv "
        " WHERE cv.vehicle_id = v.id) AS linked "
        "FROM vehicles v WHERE v.active ORDER BY v.id"
    )
    out = []
    for v in vehicles:
        # Same alias set sync and preview use (flatfile.model_aliases), so the
        # index count is what a sync of this vehicle would actually link.
        aliases = await model_aliases(conn, v["year"], str(v["make"]), v["model"])
        index = await conn.fetchval(
            "SELECT count(DISTINCT nhtsa_id) FROM comm_products "
            "WHERE model_year = $1 AND make = $2 AND model = ANY($3::text[])",
            v["year"],
            str(v["make"]).upper(),
            aliases,
        )
        out.append({**dict(v), "index": index})
    orphans = await conn.fetchval(
        "SELECT count(*) FROM communication_vehicles cv "
        "LEFT JOIN vehicles v ON v.id = cv.vehicle_id WHERE v.id IS NULL"
    )
    return out, orphans


def _table(report: Report) -> str:
    head = ("id", "vehicle", "linked comms", "index comms", "baseline", "delta", "status")
    body = [
        (
            str(r.id),
            r.identity,
            str(r.linked),
            str(r.index),
            "-" if r.baseline is None else str(r.baseline),
            "-" if r.delta is None else f"{r.delta:+d}",
            r.status,
        )
        for r in report.rows
    ]
    widths = [
        max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(head)
    ]

    def line(cells) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    return "\n".join([line(head), "-+-".join("-" * w for w in widths), *map(line, body)])


async def run(
    conn: asyncpg.Connection, baseline_path: Optional[str] = None, as_json: bool = False
) -> int:
    """Collect, report, and return the process exit code."""
    baseline = load_baseline(baseline_path) if baseline_path else None
    vehicles, orphans = await collect(conn)
    report = build_report(vehicles, orphans, baseline)
    if as_json:
        payload = {
            "rows": [asdict(r) for r in report.rows],
            "orphans": report.orphans,
            "duplicates": [
                {"year": y, "make": m, "model": mo, "ids": ids} for y, m, mo, ids in report.duplicates
            ],
            "missing_baseline_vehicles": report.missing,
            "exit_code": report.exit_code,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_table(report))
        print(f"orphaned join rows: {report.orphans}")
        print(f"duplicate active identities: {len(report.duplicates)}")
        print(
            "baseline vehicles no longer active: "
            + (", ".join(report.missing) if report.missing else "0")
        )
        print(f"exit code: {report.exit_code}")
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check no tracked vehicle lost communications.")
    parser.add_argument("--baseline", help="JSON map of vehicle id to its pre-migration count")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )

    async def _main() -> int:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                return await run(conn, baseline_path=args.baseline, as_json=args.json)
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
