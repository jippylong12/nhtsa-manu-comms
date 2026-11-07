"""Business logic for Vehicles feature.

Postgres is the only store (JIP-416). There used to be a second one, and a
mirror keeping a document in it in step with this row; both are gone, along with
the drift they allowed. `repo.tracked_vehicles` and `/api/vehicles` read the
same rows.

`comm_count` is derived from `communication_vehicles` on every read rather than
kept as a column. A stored count is exactly what let a vehicle card claim 389
communications above a detail page showing none.
"""

from datetime import datetime
from typing import Any, Optional

import asyncpg

from src.db import get_pool
from src.communications.flatfile import normalize_model
from src.jobs import repository as repo

# The wire shape in one place. comm_count is a correlated subquery rather than a
# GROUP BY join so a vehicle with no corpus still returns a row reading zero,
# instead of dropping out of the list entirely.
_VEHICLE_COLUMNS = """
    v.id, v.year, v.make, v.model, v.keywords,
    v.last_fetched_at, v.created_at, v.updated_at,
    (SELECT count(*) FROM communication_vehicles cv WHERE cv.vehicle_id = v.id)
        AS comm_count
"""


def _to_response(row: asyncpg.Record) -> dict[str, Any]:
    """Map a vehicles row onto the keys VehicleResponse expects.

    `year` goes back out as a string. It is an integer column and always was a
    string over the wire, and widening the API type would be a frontend change
    for no gain.
    """
    return {
        "id": str(row["id"]),
        "vehicle_id": row["id"],
        "year": str(row["year"]),
        "model": row["model"],
        "make": row["make"],
        "keywords": list(row["keywords"]),
        "last_fetched": row["last_fetched_at"],
        "comm_count": row["comm_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _parse_year(year: str) -> int:
    """Coerce the API's string year to the integer column, or explain why not."""
    try:
        return int(str(year).strip())
    except (TypeError, ValueError):
        raise ValueError(f"year must be a number, got {year!r}") from None


class VehicleService:
    """Service layer for vehicle operations."""

    @staticmethod
    async def create_vehicle(
        year: str,
        make: str,
        model: str,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        """Track a vehicle, or reactivate one already known.

        Identity is (year, make, model) with the model in canonical
        plain-nameplate form, so `f-150` and `F 150` are the same vehicle. The
        pipeline takes its worklist from this table, so the vehicle is visible
        to sync the moment this returns. There is no bootstrap step.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row_id, _ = await repo.upsert_vehicle(
                    conn,
                    year=_parse_year(year),
                    make=make.strip().upper(),
                    model=normalize_model(model),
                    keywords=keywords or [],
                )
            row = await conn.fetchrow(
                f"SELECT {_VEHICLE_COLUMNS} FROM vehicles v WHERE v.id = $1", row_id
            )
        return _to_response(row)

    @staticmethod
    async def get_vehicle(vehicle_id: int) -> Optional[dict[str, Any]]:
        """Get a tracked vehicle by its id, or None if it is not tracked.

        Deactivated vehicles are not found. A soft delete has to look like a
        delete from the outside, or a removed vehicle would stay addressable and
        the list and the detail endpoint would disagree about whether it exists.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_VEHICLE_COLUMNS} FROM vehicles v
                WHERE v.id = $1 AND v.active
                """,
                vehicle_id,
            )
        return _to_response(row) if row else None

    @staticmethod
    async def list_vehicles(
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List tracked vehicles with pagination, newest first."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_VEHICLE_COLUMNS} FROM vehicles v
                WHERE v.active
                ORDER BY v.created_at DESC, v.id DESC
                LIMIT $1 OFFSET $2
                """,
                per_page,
                (page - 1) * per_page,
            )
            total = await conn.fetchval("SELECT count(*) FROM vehicles WHERE active")
        return [_to_response(r) for r in rows], total

    @staticmethod
    async def update_vehicle(
        vehicle_id: int,
        keywords: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Update a vehicle's tracking configuration (its keywords).

        Unlike `create_vehicle`, an explicit empty list here does clear the
        keywords; that is what the caller asked for.
        """
        assignments: list[str] = []
        params: list[Any] = []

        if keywords is not None:
            params.append(keywords)
            assignments.append(f"keywords = ${len(params)}")

        if not assignments:
            # An empty PATCH is not an error; the caller still expects the
            # vehicle back, or a 404 if it is not tracked.
            return await VehicleService.get_vehicle(vehicle_id)

        params.append(vehicle_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Two statements rather than one UPDATE ... RETURNING wrapped in a
            # CTE: the rest of a statement reads the snapshot taken before its
            # data-modifying CTE ran, so a single statement would hand back the
            # row as it was before the update.
            row_id = await conn.fetchval(
                f"""
                UPDATE vehicles SET {", ".join(assignments)}
                WHERE id = ${len(params)} AND active
                RETURNING id
                """,
                *params,
            )
            if row_id is None:
                return None
            row = await conn.fetchrow(
                f"SELECT {_VEHICLE_COLUMNS} FROM vehicles v WHERE v.id = $1", row_id
            )
        return _to_response(row)

    @staticmethod
    async def delete_vehicle(vehicle_id: int) -> bool:
        """Stop tracking a vehicle. Returns False if it was not tracked.

        Deactivates rather than deletes. `communication_vehicles` hangs off this
        row with ON DELETE CASCADE, and a bulletin shared with other vehicles
        would take their links with it. `tracked_vehicles` filters on `active`,
        so clearing the flag is enough to stop the pipeline polling it.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE vehicles SET active = false
                WHERE id = $1 AND active
                RETURNING id
                """,
                vehicle_id,
            )
        return row is not None

    @staticmethod
    async def touch_fetched_at(vehicle_id: int, at: Optional[datetime] = None) -> None:
        """Record that NHTSA was just asked about this vehicle."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE vehicles SET last_fetched_at = coalesce($2, now()) "
                "WHERE id = $1",
                vehicle_id,
                at,
            )
