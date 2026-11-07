"""Upsert helpers for the canonical store.

Kept separate from the sync job itself so the idempotency rules can be tested
directly without standing up an NHTSA client.
"""

from datetime import datetime
from typing import Any, Optional

import asyncpg


async def upsert_communication(
    conn: asyncpg.Connection,
    *,
    nhtsa_id: str,
    communication_number: Optional[str],
    communication_type: Optional[str],
    communication_date: Optional[datetime],
    summary: Optional[str],
    details_summary: Optional[str],
    raw: str,
) -> tuple[int, bool]:
    """Insert or refresh a canonical communication.

    Returns ``(id, was_inserted)``.

    The metadata columns are refreshed on conflict, but `status`,
    `status_reason`, `attempts`, and `processed_at` are deliberately left
    untouched: re-running sync must never drag an already-processed row back to
    `pending` and cause the processor to pay for it a second time.

    `xmax = 0` is the standard way to tell an INSERT from an UPDATE in a
    RETURNING clause; on a freshly inserted row the deleting-transaction id is
    zero.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO communications (
            nhtsa_id, communication_number, communication_type,
            communication_date, summary, details_summary, raw, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'pending')
        ON CONFLICT (nhtsa_id) DO UPDATE SET
            communication_number = EXCLUDED.communication_number,
            communication_type   = EXCLUDED.communication_type,
            communication_date   = EXCLUDED.communication_date,
            summary              = EXCLUDED.summary,
            details_summary      = EXCLUDED.details_summary,
            raw                  = EXCLUDED.raw
        RETURNING id, (xmax = 0) AS inserted
        """,
        nhtsa_id,
        communication_number,
        communication_type,
        communication_date,
        summary,
        details_summary,
        raw,
    )
    return row["id"], row["inserted"]


async def upsert_document(
    conn: asyncpg.Connection,
    *,
    communication_id: int,
    url: str,
    doc_summary: Optional[str],
    load_date: Optional[datetime],
) -> bool:
    """Register a PDF for later processing. Returns True if newly inserted.

    DO NOTHING rather than DO UPDATE: an existing row may already carry
    extracted text, LLM output, and an embedding, none of which should be
    disturbed because sync saw the same URL again.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO comm_documents (communication_id, url, doc_summary, load_date)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (url) DO NOTHING
        RETURNING id
        """,
        communication_id,
        url,
        doc_summary,
        load_date,
    )
    return row is not None


async def link_vehicle(
    conn: asyncpg.Connection,
    *,
    communication_id: int,
    vehicle_id: int,
    matched_keywords: list[str],
) -> bool:
    """Attach a communication to a vehicle. Returns True if the link is new."""
    row = await conn.fetchrow(
        """
        INSERT INTO communication_vehicles (communication_id, vehicle_id, matched_keywords)
        VALUES ($1, $2, $3)
        ON CONFLICT (communication_id, vehicle_id) DO NOTHING
        RETURNING communication_id
        """,
        communication_id,
        vehicle_id,
        matched_keywords,
    )
    return row is not None


async def upsert_vehicle(
    conn: asyncpg.Connection,
    *,
    year: int,
    make: str,
    model: str,
    keywords: Optional[list[str]] = None,
) -> tuple[int, bool]:
    """Track a vehicle by identity ``(year, make, model)``. Returns ``(id, was_inserted)``.

    `model` is expected in canonical plain-nameplate form (``normalize_model``).
    Select-then-write rather than ON CONFLICT: the unique index is partial
    (``WHERE active``), so ON CONFLICT would insert a second row beside an
    inactive twin instead of reviving it. The caller owns the transaction.

    An existing row is reactivated, and keeps its keywords when the caller
    supplied none: an add with no keywords never clears the ones already
    configured. `update_vehicle` is the way to change them on purpose.

    Two adds of the same new identity racing (a double-clicked Add) both miss
    the SELECT; the loser's write then fails on the partial unique index once
    the winner commits. The write runs in a savepoint and is retried once, when
    the winner's row is visible and is simply reused, so the race returns the
    same vehicle to both callers instead of a 500.
    """
    keywords = keywords or []
    try:
        async with conn.transaction():
            return await _upsert_vehicle_once(conn, year, make, model, keywords)
    except asyncpg.UniqueViolationError:
        return await _upsert_vehicle_once(conn, year, make, model, keywords)


async def _upsert_vehicle_once(
    conn: asyncpg.Connection, year: int, make: str, model: str, keywords: list[str]
) -> tuple[int, bool]:
    """One select-then-write pass of `upsert_vehicle`."""
    existing = await conn.fetchval(
        """
        SELECT id FROM vehicles
        WHERE year = $1 AND make = $2 AND model = $3
        ORDER BY active DESC, id
        LIMIT 1
        """,
        year,
        make,
        model,
    )
    if existing is not None:
        await conn.execute(
            """
            UPDATE vehicles
            SET active = true,
                keywords = CASE WHEN $2::text[] = '{}'::text[] THEN keywords ELSE $2 END,
                updated_at = now()
            WHERE id = $1
            """,
            existing,
            keywords,
        )
        return existing, False
    row_id = await conn.fetchval(
        """
        INSERT INTO vehicles (year, make, model, keywords)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        year,
        make,
        model,
        keywords,
    )
    return row_id, True


def vehicle_scope(param: str, comm_id: str = "c.id") -> str:
    """SQL restricting a stage query to one tracked vehicle, or to all of them.

    Written once because the four stage queries have to agree exactly: an
    in-app refresh quotes a price for one vehicle's backlog and must then
    process that backlog and no other, or the bill will not match the estimate.

    `param` is the ``$n`` placeholder holding the vehicles.id row id. A NULL there
    means "every vehicle", which is what the CLI jobs pass.
    """
    return (
        f"({param}::bigint IS NULL OR EXISTS ("
        f" SELECT 1 FROM communication_vehicles cv"
        f" JOIN vehicles v ON v.id = cv.vehicle_id"
        f" WHERE cv.communication_id = {comm_id} AND v.id = {param}::bigint))"
    )


async def existing_nhtsa_ids(conn: asyncpg.Connection, nhtsa_ids: list[str]) -> set[str]:
    """Return the subset of ids already present, so sync can skip refetching them."""
    if not nhtsa_ids:
        return set()
    rows = await conn.fetch(
        "SELECT nhtsa_id FROM communications WHERE nhtsa_id = ANY($1::text[])", nhtsa_ids
    )
    return {r["nhtsa_id"] for r in rows}


async def tracked_vehicles(
    conn: asyncpg.Connection, only_active: bool = True
) -> list[dict[str, Any]]:
    """Return tracked vehicles from the canonical store."""
    rows = await conn.fetch(
        f"""
        SELECT id, year, make, model, keywords
        FROM vehicles
        {"WHERE active" if only_active else ""}
        ORDER BY year, model
        """
    )
    return [dict(r) for r in rows]
