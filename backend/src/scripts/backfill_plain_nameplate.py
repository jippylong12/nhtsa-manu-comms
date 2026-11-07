"""One-time backfill of tracked vehicles to the plain nameplate (ADR 0001, JIP-433).

Rows written under the crash-test taxonomy (``F-150 REGULAR CAB``) match nothing in
``comm_products``; this rewrites each active vehicle's ``model`` to the plain
nameplate the flat-file index uses (``F 150``), merging vehicles that collapse to
one identity.

Mapping (per active vehicle): take the vocabulary V of ``comm_products.model`` for
the vehicle's year and make, and ``c = normalize_model(model)``. Pick the first of:
exact ``c`` in V; the longest ``v`` in V that ``c`` starts with (``v + " "``);
the ``v`` whose compact form equals, or is a prefix of, ``compact_model(c)``
(longest wins). No match -> UNMAPPED: left untouched and reported, never a failure.

Merge: vehicles sharing a target identity collapse into the lowest id; links are
repointed (earliest ``first_seen_at``, union of keywords), keywords unioned, the
absorbed rows deleted. Ordering inside ``--apply`` (one transaction) keeps the
partial unique index ``vehicles_active_identity_uidx`` from ever seeing a duplicate.

Usage:
    python -m src.scripts.backfill_plain_nameplate              # dry-run (default)
    python -m src.scripts.backfill_plain_nameplate --apply      # write, one transaction
    python -m src.scripts.backfill_plain_nameplate --json       # machine-readable report
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass

import asyncpg

from src.communications.flatfile import compact_model, normalize_model
from src.db import close_pool, get_pool

log = logging.getLogger("backfill_plain_nameplate")


@dataclass
class Row:
    """One active vehicle's planned outcome."""

    id: int
    year: int
    make: str
    before: str
    after: str
    action: str  # keep | rename | merge-into <id> | UNMAPPED
    links_before: int
    links_after: int


def choose_target(canonical: str, vocabulary: list[str]) -> str | None:
    """The plain nameplate for ``canonical`` within ``vocabulary``, or None."""
    c = normalize_model(canonical)
    if not c:
        return None
    vocab = {normalize_model(v) for v in vocabulary} - {""}
    if c in vocab:
        return c
    prefixed = [v for v in vocab if c.startswith(v + " ")]
    if prefixed:
        return max(prefixed, key=lambda v: (len(v), v))
    cc = compact_model(c)
    compact = [v for v in vocab if compact_model(v) and cc.startswith(compact_model(v))]
    if compact:
        return max(compact, key=lambda v: (len(compact_model(v)), v))
    return None


async def plan(conn: asyncpg.Connection) -> list[Row]:
    """Read-only: what ``apply`` would do to each active vehicle."""
    vehicles = await conn.fetch(
        "SELECT v.id, v.year, v.make, v.model, "
        "(SELECT count(*) FROM communication_vehicles cv WHERE cv.vehicle_id = v.id) AS links "
        "FROM vehicles v WHERE v.active ORDER BY v.id"
    )
    vocab: dict[tuple[int, str], list[str]] = {}
    targets: dict[int, str | None] = {}
    for v in vehicles:
        key = (v["year"], v["make"])
        if key not in vocab:
            rows = await conn.fetch(
                "SELECT DISTINCT model FROM comm_products WHERE model_year=$1 AND make=$2",
                *key,
            )
            vocab[key] = [r["model"] for r in rows]
        targets[v["id"]] = choose_target(normalize_model(v["model"]), vocab[key])

    groups: dict[tuple[int, str, str], list[int]] = {}
    for v in vehicles:
        if targets[v["id"]] is not None:
            groups.setdefault((v["year"], v["make"], targets[v["id"]]), []).append(v["id"])

    union_links: dict[int, int] = {}
    for ids in groups.values():
        union_links[ids[0]] = await conn.fetchval(
            "SELECT count(DISTINCT communication_id) FROM communication_vehicles "
            "WHERE vehicle_id = ANY($1::bigint[])",
            ids,
        )

    out: list[Row] = []
    for v in vehicles:
        target = targets[v["id"]]
        if target is None:
            out.append(
                Row(
                    v["id"],
                    v["year"],
                    v["make"],
                    v["model"],
                    v["model"],
                    "UNMAPPED",
                    v["links"],
                    v["links"],
                )
            )
            continue
        survivor = groups[(v["year"], v["make"], target)][0]
        if v["id"] != survivor:
            out.append(
                Row(
                    v["id"],
                    v["year"],
                    v["make"],
                    v["model"],
                    target,
                    f"merge-into {survivor}",
                    v["links"],
                    0,
                )
            )
        else:
            action = "keep" if v["model"] == target else "rename"
            out.append(
                Row(
                    v["id"],
                    v["year"],
                    v["make"],
                    v["model"],
                    target,
                    action,
                    v["links"],
                    union_links[survivor],
                )
            )
    return out


async def apply(conn: asyncpg.Connection, rows: list[Row]) -> None:
    """Write the plan in one transaction: repoint links, delete absorbed, rename."""
    absorbed: dict[int, list[int]] = {}
    for r in rows:
        if r.action.startswith("merge-into "):
            absorbed.setdefault(int(r.action.split()[1]), []).append(r.id)

    async with conn.transaction():
        for survivor, ids in absorbed.items():
            await conn.execute(
                """
                INSERT INTO communication_vehicles AS cv
                    (communication_id, vehicle_id, matched_keywords, first_seen_at)
                SELECT s.communication_id, $1,
                       ARRAY(SELECT DISTINCT k FROM communication_vehicles c2,
                                    unnest(c2.matched_keywords) k
                             WHERE c2.communication_id = s.communication_id
                               AND c2.vehicle_id = ANY($2::bigint[]) ORDER BY k),
                       min(s.first_seen_at)
                FROM communication_vehicles s
                WHERE s.vehicle_id = ANY($2::bigint[])
                GROUP BY s.communication_id
                ON CONFLICT (communication_id, vehicle_id) DO UPDATE SET
                    first_seen_at = LEAST(cv.first_seen_at, EXCLUDED.first_seen_at),
                    matched_keywords = ARRAY(
                        SELECT DISTINCT k FROM unnest(
                            cv.matched_keywords || EXCLUDED.matched_keywords) k ORDER BY k)
                """,
                survivor,
                ids,
            )
            await conn.execute(
                "UPDATE vehicles SET keywords = ARRAY(SELECT DISTINCT k FROM unnest("
                "keywords || (SELECT coalesce(array_agg(x), '{}') FROM vehicles a, "
                "unnest(a.keywords) x WHERE a.id = ANY($2::bigint[]))) k ORDER BY k), "
                "active = active OR EXISTS (SELECT 1 FROM vehicles a "
                "WHERE a.id = ANY($2::bigint[]) AND a.active), updated_at = now() "
                "WHERE id = $1",
                survivor,
                ids,
            )
            # refresh_runs cascades on vehicle delete; repoint it so the absorbed
            # rows' refresh history and recorded spend move to the survivor.
            await conn.execute(
                "UPDATE refresh_runs SET vehicle_id = $1 WHERE vehicle_id = ANY($2::bigint[])",
                survivor,
                ids,
            )
            await conn.execute(
                "DELETE FROM communication_vehicles WHERE vehicle_id = ANY($1::bigint[])", ids
            )
            await conn.execute("DELETE FROM vehicles WHERE id = ANY($1::bigint[])", ids)
        for r in rows:
            if r.action == "rename":
                await conn.execute(
                    "UPDATE vehicles SET model = $2, updated_at = now() WHERE id = $1",
                    r.id,
                    r.after,
                )


def _table(rows: list[Row]) -> str:
    head = (
        "id",
        "year",
        "make",
        "model (before)",
        "model (after)",
        "action",
        "links before",
        "links after",
    )
    body = [tuple(str(x) for x in asdict(r).values()) for r in rows]
    widths = [
        max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(head)
    ]

    def line(cells) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    return "\n".join([line(head), "-+-".join("-" * w for w in widths), *map(line, body)])


_write = apply


async def run(conn: asyncpg.Connection, apply: bool = False, as_json: bool = False) -> int:
    """Plan, report, and (with ``apply``) write. Returns the process exit code."""
    if apply and not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM comm_products)"):
        print(
            "comm_products is empty: bootstrap the index first "
            "(python -m src.communications.flatfile --bootstrap).",
            file=sys.stderr,
        )
        return 2
    rows = await plan(conn)
    print(json.dumps([asdict(r) for r in rows], indent=2) if as_json else _table(rows))
    unmapped = [r for r in rows if r.action == "UNMAPPED"]
    if unmapped:
        print(
            f"{len(unmapped)} UNMAPPED vehicle(s) left unchanged: re-pick in the UI.",
            file=sys.stderr,
        )
    if apply:
        await _write(conn, rows)
        print("Applied.", file=sys.stderr)
    else:
        print("Dry run: nothing changed. Use --apply to write.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill tracked vehicles to the plain nameplate."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="write the changes")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )

    async def _main() -> int:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                return await run(conn, apply=args.apply, as_json=args.json)
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
