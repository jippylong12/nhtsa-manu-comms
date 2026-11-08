"""Business logic for the signed-in user's profile."""

import logging

from src.billing.entitlements import access_for
from src.corpus import service as corpus_service
from src.db import get_pool
from src.public import service as public_service
from src.refresh import service as refresh_service
from src.vehicles.service import VehicleService

logger = logging.getLogger(__name__)


async def list_entitlements(user_id: int) -> list[dict]:
    """The user's entitlements, oldest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT kind, vehicle_id, status, current_period_end FROM entitlements "
            "WHERE user_id = $1 ORDER BY id",
            user_id,
        )
    return [dict(r) for r in rows]


MAX_VEHICLES = 10
SAMPLE_QUOTA = {"serious": 1, "medium": 2, "small": 3}
_ALL_ROWS = 1_000_000


class VehicleLimitReached(Exception):
    """The account already links the maximum number of vehicles."""


async def _my_vehicle(conn, user_id: int, vehicle_id: int) -> dict | None:
    """One of the caller's linked vehicles as a MyVehicle dict, or None when not linked."""
    row = await conn.fetchrow(
        """
        SELECT v.id AS vehicle_id, v.year, v.make, v.model, uv.created_at AS added_at
        FROM user_vehicles uv JOIN vehicles v ON v.id = uv.vehicle_id
        WHERE uv.user_id = $1 AND uv.vehicle_id = $2 AND v.active
        """,
        user_id,
        vehicle_id,
    )
    if row is None:
        return None
    found = await public_service.counts_for(conn, vehicle_id)
    return {
        **dict(row),
        "counts": found["counts"],
        "availability": "ready" if sum(found["counts"].values()) else "processing",
        "access": await access_for(conn, user_id, vehicle_id),
    }


async def list_my_vehicles(user_id: int) -> list[dict]:
    """The caller's linked vehicles, oldest link first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        ids = await conn.fetch(
            "SELECT uv.vehicle_id FROM user_vehicles uv JOIN vehicles v ON v.id = uv.vehicle_id "
            "WHERE uv.user_id = $1 AND v.active ORDER BY uv.created_at, uv.vehicle_id",
            user_id,
        )
        return [await _my_vehicle(conn, user_id, r["vehicle_id"]) for r in ids]


async def add_vehicle(user_id: int, year: int, make: str, model: str) -> dict:
    """Track the identity, link it to the caller (idempotent) and start ingest if it is empty.

    Raises ValueError for a bad identity and VehicleLimitReached past MAX_VEHICLES.
    """
    vehicle = await VehicleService.create_vehicle(year=str(year), make=make, model=model)
    vehicle_id = vehicle["vehicle_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialise this user's adds so two requests cannot both slip under the cap.
            await conn.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", user_id)
            linked = await conn.fetchval(
                "SELECT count(*) FROM user_vehicles WHERE user_id = $1", user_id
            )
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2)",
                user_id,
                vehicle_id,
            )
            if not exists and linked >= MAX_VEHICLES:
                raise VehicleLimitReached()
            await conn.execute(
                "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                user_id,
                vehicle_id,
            )
        result = await _my_vehicle(conn, user_id, vehicle_id)
    if sum(result["counts"].values()) == 0:
        try:
            await refresh_service.start(vehicle_id)
        except refresh_service.RefreshConflict:
            logger.info("ingest deferred to the nightly run (vehicle %s)", vehicle_id)
    return result


async def remove_vehicle(user_id: int, vehicle_id: int) -> bool:
    """Unlink the vehicle from the caller only. False when the caller had no such link."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2 RETURNING 1",
            user_id,
            vehicle_id,
        )
    return deleted is not None


def _newest_first(rows: list[dict]) -> None:
    """Order by communication_date DESC NULLS LAST, nhtsa_id DESC, in place."""
    rows.sort(key=lambda r: r["nhtsa_id"], reverse=True)
    rows.sort(key=lambda r: r["communication_date"] is None)  # stable: undated last
    dated = [r for r in rows if r["communication_date"] is not None]
    dated.sort(key=lambda r: r["communication_date"], reverse=True)
    rows[: len(dated)] = dated


def sample_ids(rows: list[dict]) -> set[str]:
    """The nhtsa_ids that stay readable on the free sample.

    `rows` is the whole revision-folded list for the vehicle, already ordered
    newest first; the quota is applied per severity across all of it.
    """
    taken = dict.fromkeys(SAMPLE_QUOTA, 0)
    keep: set[str] = set()
    for row in rows:
        sev = row["severity"]
        if taken.get(sev, SAMPLE_QUOTA.get(sev, 0)) < SAMPLE_QUOTA.get(sev, 0):
            taken[sev] += 1
            keep.add(row["nhtsa_id"])
    return keep


async def _details(conn, nhtsa_ids: list[str]) -> dict[str, dict]:
    """Document-derived fields for the given bulletins, in one query."""
    if not nhtsa_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT c.nhtsa_id, d.url, d.symptoms, d.systems, d.components, d.remedy,
               d.applicability, d.applicability_years, d.applicability_models,
               d.applicability_conditions, d.audience
        FROM comm_documents d JOIN communications c ON c.id = d.communication_id
        WHERE c.nhtsa_id = ANY($1::text[])
        ORDER BY d.id
        """,
        nhtsa_ids,
    )
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(
            r["nhtsa_id"],
            {
                "symptoms": [], "systems": [], "components": [], "applicability_years": [],
                "applicability_models": [], "documents": [], "remedy": None,
                "applicability": None, "applicability_conditions": None, "audience": None,
            },
        )
        for key in ("symptoms", "systems", "components", "applicability_years",
                    "applicability_models"):
            d[key].extend(x for x in r[key] if x not in d[key])
        for key in ("remedy", "applicability", "applicability_conditions", "audience"):
            d[key] = d[key] or r[key]
        d["documents"].append({"url": r["url"]})
    return out


def _empty_details() -> dict:
    return {
        "symptoms": [], "systems": [], "components": [], "applicability_years": [],
        "applicability_models": [], "documents": [], "remedy": None, "applicability": None,
        "applicability_conditions": None, "audience": None,
    }


async def my_bulletins(
    user_id: int,
    vehicle_id: int,
    severity: str | None,
    page: int,
    per_page: int,
    search: str | None = None,
) -> dict | None:
    """The caller's folded bulletins for one linked vehicle, or None when it is not linked.

    Under the sample rule the readable set is chosen across the whole folded list,
    before the severity filter, the text search and the page are applied. A search
    only ever matches rows that are readable, so a locked row's text cannot leak
    through which rows match.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        linked = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM user_vehicles uv JOIN vehicles v ON v.id = uv.vehicle_id "
            "WHERE uv.user_id = $1 AND uv.vehicle_id = $2 AND v.active)",
            user_id,
            vehicle_id,
        )
        if not linked:
            return None
        access = await access_for(conn, user_id, vehicle_id)

        rows, _ = await corpus_service.list_communications(
            vehicle_id=vehicle_id,
            status="processed",
            revisions="latest",
            page=1,
            per_page=_ALL_ROWS,
        )
        _newest_first(rows)
        readable = {r["nhtsa_id"] for r in rows} if access == "full" else sample_ids(rows)
        sample_counts = dict.fromkeys(SAMPLE_QUOTA, 0)
        for r in rows:
            if r["nhtsa_id"] in readable and r["severity"] in sample_counts:
                sample_counts[r["severity"]] += 1

        shown = [r for r in rows if severity is None or r["severity"] == severity]
        if search:
            found, _ = await corpus_service.list_communications(
                vehicle_id=vehicle_id,
                status="processed",
                search=search,
                revisions="latest",
                page=1,
                per_page=_ALL_ROWS,
            )
            matched = {r["nhtsa_id"] for r in found}
            shown = [r for r in shown if r["nhtsa_id"] in readable and r["nhtsa_id"] in matched]
        window = shown[(page - 1) * per_page : page * per_page]
        details = await _details(conn, [r["nhtsa_id"] for r in window if r["nhtsa_id"] in readable])

    items = []
    for r in window:
        identity = {
            "nhtsa_id": r["nhtsa_id"],
            "communication_number": r["communication_number"],
            "communication_type": r["communication_type"],
            "communication_date": r["communication_date"],
            "severity": r["severity"],
            "superseded_count": r["superseded_count"],
        }
        if r["nhtsa_id"] in readable:
            items.append(
                {
                    **identity,
                    "locked": False,
                    "summary": r["summary"],
                    "llm_summary": r["llm_summary"],
                    **{**_empty_details(), **details.get(r["nhtsa_id"], {})},
                }
            )
        else:
            items.append({**identity, "locked": True})
    return {
        "items": items,
        "total": len(shown),
        "page": page,
        "per_page": per_page,
        "access": access,
        "sample_counts": sample_counts,
    }
