"""Business logic for the public lookup: what a visitor sees before signing up."""

import re
import time
from typing import Any

import asyncpg

from src.communications.flatfile import normalize_model
from src.db import get_pool
from src.symptom_search.retrieval import REVISION_STEM_SQL

EXAMPLE_TTL_SECONDS = 600
SEVERITIES = ("serious", "medium", "small")

# Module-level (expires_at, value) so one process answers the example query once per
# TTL. Tests clear it with reset_example_cache().
_example_cache: tuple[float, dict | None] | None = None


def reset_example_cache() -> None:
    """Forget the cached example vehicle."""
    global _example_cache
    _example_cache = None


async def counts_for(conn: asyncpg.Connection, vehicle_id: int) -> dict[str, Any]:
    """Revision-family counts by severity for one vehicle, plus its latest bulletin date.

    A family is one `revision_stem`, represented by its latest revision, among the
    vehicle's processed communications. Returns
    `{"counts": {serious, medium, small}, "latest_date": datetime | None}`.
    """
    rows = await conn.fetch(
        f"""
        SELECT severity, count(*) AS n, max(communication_date) AS latest
        FROM (
            SELECT DISTINCT ON (stem) severity, communication_date
            FROM (
                SELECT c.severity, c.communication_date, c.nhtsa_id,
                       {REVISION_STEM_SQL} AS stem
                FROM communications c
                JOIN communication_vehicles cv ON cv.communication_id = c.id
                WHERE cv.vehicle_id = $1 AND c.status = 'processed'
            ) c
            ORDER BY stem, communication_date DESC NULLS LAST, nhtsa_id DESC
        ) fam
        GROUP BY severity
        """,
        vehicle_id,
    )
    counts = dict.fromkeys(SEVERITIES, 0)
    latest = None
    for r in rows:
        counts[r["severity"]] = r["n"]
        if r["latest"] is not None and (latest is None or r["latest"] > latest):
            latest = r["latest"]
    return {"counts": counts, "latest_date": latest}


async def _example(conn: asyncpg.Connection) -> dict | None:
    """The active vehicle with the most processed revision families, cached."""
    global _example_cache
    now = time.monotonic()
    if _example_cache and _example_cache[0] > now:
        return _example_cache[1]
    row = await conn.fetchrow(
        f"""
        SELECT v.id, v.year, v.make, v.model
        FROM (
            SELECT vehicle_id, count(*) AS n
            FROM (
                SELECT DISTINCT ON (cv.vehicle_id, stem) cv.vehicle_id, stem
                FROM (
                    SELECT c.id, c.nhtsa_id, c.communication_date,
                           {REVISION_STEM_SQL} AS stem
                    FROM communications c WHERE c.status = 'processed'
                ) c
                JOIN communication_vehicles cv ON cv.communication_id = c.id
                ORDER BY cv.vehicle_id, stem
            ) fam
            GROUP BY vehicle_id
        ) t
        JOIN vehicles v ON v.id = t.vehicle_id AND v.active
        ORDER BY t.n DESC, v.id
        LIMIT 1
        """
    )
    value = None
    if row:
        found = await counts_for(conn, row["id"])
        value = {
            "vehicle_id": row["id"],
            "year": row["year"],
            "make": row["make"],
            "model": row["model"],
            **found,
        }
    _example_cache = (now + EXAMPLE_TTL_SECONDS, value)
    return value


async def lookup(year: int, make: str, model: str) -> dict[str, Any]:
    """Look a vehicle up by identity; an untracked one is a normal answer, not an error."""
    make = make.strip().upper()
    model = normalize_model(model)
    pool = await get_pool()
    async with pool.acquire() as conn:
        vid = await conn.fetchval(
            "SELECT id FROM vehicles WHERE year = $1 AND make = $2 AND model = $3 AND active",
            year,
            make,
            model,
        )
        result: dict[str, Any] = {
            "vehicle_id": vid,
            "year": year,
            "make": make,
            "model": model,
            "counts": dict.fromkeys(SEVERITIES, 0),
            "latest_date": None,
            "example": None,
        }
        if vid is None:
            result["availability"] = "unknown"
            result["example"] = await _example(conn)
            return result
        result.update(await counts_for(conn, vid))
        total = sum(result["counts"].values())
        result["availability"] = "ready" if total else "processing"
        return result


_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


async def unsubscribe(token: str) -> str | None:
    """Mark the token's owner unsubscribed. `unsubscribed`, `already`, or None when unknown."""
    if not _TOKEN_SHAPE.match(token):
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH owner AS (
                SELECT user_id, unsubscribed_at FROM alert_state
                WHERE unsubscribe_token = $1 FOR UPDATE
            ), upd AS (
                UPDATE alert_state a SET unsubscribed_at = now()
                FROM owner WHERE a.user_id = owner.user_id AND owner.unsubscribed_at IS NULL
                RETURNING a.user_id
            )
            SELECT (SELECT unsubscribed_at IS NOT NULL FROM owner) AS was_off,
                   EXISTS (SELECT 1 FROM owner) AS found
            """,
            token,
        )
    if not row["found"]:
        return None
    return "already" if row["was_off"] else "unsubscribed"
