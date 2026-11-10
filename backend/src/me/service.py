"""Business logic for the signed-in user's profile."""

import logging
from datetime import date, datetime, timezone

from src.billing.entitlements import access_for
from src.communications.flatfile import normalize_model
from src.config import get_settings
from src.corpus import service as corpus_service
from src.db import get_pool
from src.public import service as public_service
from src.refresh import queue as ingest_queue
from src.symptom_search.retrieval import REVISION_STEM_SQL
from src.vehicles.service import VehicleService

logger = logging.getLogger(__name__)


async def list_entitlements(user_id: int) -> list[dict]:
    """The user's entitlements, oldest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT kind, plan, vehicle_id, status, current_period_end, cancel_at_period_end "
            "FROM entitlements "
            "WHERE user_id = $1 ORDER BY id",
            user_id,
        )
    return [dict(r) for r in rows]


class NotEligibleForAlerts(Exception):
    """Alerts are a subscriber feature; this user has no active subscription and is no admin."""


async def _alerts(conn, user: dict) -> dict:
    """Alert state for one user, creating the `alert_state` row on first need."""
    row = await conn.fetchrow(
        """
        INSERT INTO alert_state (user_id) VALUES ($1)
        ON CONFLICT (user_id) DO UPDATE SET user_id = alert_state.user_id
        RETURNING unsubscribed_at, last_sent_at
        """,
        user["id"],
    )
    eligible = bool(user["is_admin"]) or bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM entitlements WHERE user_id = $1 "
            "AND kind = 'subscription' AND status = 'active')",
            user["id"],
        )
    )
    return {
        "enabled": row["unsubscribed_at"] is None,
        "last_sent_at": row["last_sent_at"],
        "eligible": eligible,
    }


async def get_alerts(user: dict) -> dict:
    """`{enabled, last_sent_at, eligible}` for GET /api/me."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _alerts(conn, user)


async def set_alerts(user: dict, enabled: bool) -> dict:
    """Turn alerts on (clears `unsubscribed_at`) or off. On needs eligibility; off never does."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await _alerts(conn, user)
        if enabled and not status["eligible"]:
            raise NotEligibleForAlerts
        await conn.execute(
            "UPDATE alert_state SET unsubscribed_at = "
            "CASE WHEN $2 THEN NULL ELSE COALESCE(unsubscribed_at, now()) END WHERE user_id = $1",
            user["id"],
            enabled,
        )
        return await _alerts(conn, user)


MAX_VEHICLES = 10
SAMPLE_QUOTA = {"serious": 1, "medium": 2, "small": 3}
_ALL_ROWS = 1_000_000


class VehicleLimitReached(Exception):
    """The account already links the maximum number of vehicles."""


# A free account may sample this many different cars, ever (see migration 0018).
FREE_SAMPLE_VEHICLES = 2


class SampleLimitReached(Exception):
    """A free account has used its free samples and is adding another car."""


# How many cars each subscription covers at a time. A car the account has unlocked
# ($3, forever) is its own purchase and does not take one of these places.
PLAN_VEHICLES = {"monthly": 2, "all_access": 20}


class PlanLimitReached(Exception):
    """A subscriber already has as many cars as the plan covers."""

    def __init__(self, plan: str) -> None:
        super().__init__(plan)
        self.plan = plan
        self.limit = PLAN_VEHICLES[plan]


async def _add_refusal(conn, user_id: int, year: int, make: str, model: str) -> Exception | None:
    """Why this account may not add this car, or None when it may.

    A car already on the account is always fine (adding is idempotent). A subscriber is
    held to the plan's car count, not counting unlocked cars. Everyone else is held to
    MAX_VEHICLES, and a free account also to the free-sample rule.
    """
    row = await conn.fetchrow(
        """
        SELECT
            EXISTS (SELECT 1 FROM users u WHERE u.id = $1 AND u.is_admin) AS admin,
            (SELECT COALESCE(e.plan, 'monthly') FROM entitlements e
              WHERE e.user_id = $1 AND e.kind = 'subscription' AND e.status = 'active'
              ORDER BY (e.plan = 'all_access') DESC LIMIT 1) AS plan,
            EXISTS (
                SELECT 1 FROM user_vehicles uv JOIN vehicles v ON v.id = uv.vehicle_id
                WHERE uv.user_id = $1 AND v.year = $2 AND v.make = $3 AND v.model = $4
            ) AS linked,
            (SELECT count(*) FROM user_vehicles WHERE user_id = $1) AS total,
            (SELECT count(*) FROM user_vehicles uv
              WHERE uv.user_id = $1 AND NOT EXISTS (
                  SELECT 1 FROM entitlements e
                  WHERE e.user_id = $1 AND e.kind = 'unlock' AND e.status = 'active'
                    AND e.vehicle_id = uv.vehicle_id)) AS on_plan
        """,
        user_id,
        year,
        make.strip().upper(),
        normalize_model(model),
    )
    if row["linked"]:
        return None
    if row["plan"] in PLAN_VEHICLES and not row["admin"]:
        full = row["on_plan"] >= PLAN_VEHICLES[row["plan"]]
        return PlanLimitReached(row["plan"]) if full else None
    if row["total"] >= MAX_VEHICLES:
        return VehicleLimitReached()
    if not await _sample_slot_free(conn, user_id, year, make, model):
        return SampleLimitReached()
    return None


async def _sample_slot_free(conn, user_id: int, year: int, make: str, model: str) -> bool:
    """May this account add this car under the free-sample rule?

    Yes for an admin or an active subscriber, for a car the account was already granted,
    and while fewer than FREE_SAMPLE_VEHICLES grants are in use. A grant is not in use
    once the account holds an active unlock for that car.
    """
    return await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM users u WHERE u.id = $1 AND u.is_admin)
            OR EXISTS (
                SELECT 1 FROM entitlements e
                WHERE e.user_id = $1 AND e.kind = 'subscription' AND e.status = 'active')
            OR EXISTS (
                SELECT 1 FROM sample_grants g JOIN vehicles v ON v.id = g.vehicle_id
                WHERE g.user_id = $1 AND v.year = $3 AND v.make = $4 AND v.model = $5)
            OR (
                SELECT count(*) FROM sample_grants g
                WHERE g.user_id = $1 AND NOT EXISTS (
                    SELECT 1 FROM entitlements e
                    WHERE e.user_id = $1 AND e.kind = 'unlock' AND e.status = 'active'
                      AND e.vehicle_id = g.vehicle_id)
            ) < $2
        """,
        user_id,
        FREE_SAMPLE_VEHICLES,
        year,
        make.strip().upper(),
        normalize_model(model),
    )


async def _my_vehicle(conn, user_id: int, vehicle_id: int) -> dict | None:
    """One of the caller's linked vehicles as a MyVehicle dict, or None when not linked."""
    row = await conn.fetchrow(
        """
        SELECT v.id AS vehicle_id, v.year, v.make, v.model, uv.created_at AS added_at,
               uv.alerts_enabled
        FROM user_vehicles uv JOIN vehicles v ON v.id = uv.vehicle_id
        WHERE uv.user_id = $1 AND uv.vehicle_id = $2 AND v.active
        """,
        user_id,
        vehicle_id,
    )
    if row is None:
        return None
    found = await public_service.counts_for(conn, vehicle_id)
    reads = await counts_for_user(conn, user_id, vehicle_id)
    return {
        **dict(row),
        "counts": found["counts"],
        "unread": reads["unread"],
        "new_this_week": reads["new_this_week"],
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

    Raises ValueError for a bad identity, VehicleLimitReached past MAX_VEHICLES,
    SampleLimitReached when a free account has used its free samples, and
    PlanLimitReached when a subscriber already has the cars the plan covers.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Refuse before creating anything: a capped account must not be able to
        # create tracked vehicles (which the nightly pipeline ingests) by collecting refusals.
        refusal = await _add_refusal(conn, user_id, year, make, model)
    if refusal:
        raise refusal
    vehicle = await VehicleService.create_vehicle(year=str(year), make=make, model=model)
    vehicle_id = vehicle["vehicle_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialise this user's adds so two requests cannot both slip under the cap.
            await conn.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", user_id)
            # Re-checked under the user lock so two adds cannot both take the last place.
            refusal = await _add_refusal(conn, user_id, year, make, model)
            if refusal:
                raise refusal
            await conn.execute(
                "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                user_id,
                vehicle_id,
            )
            await conn.execute(
                "INSERT INTO sample_grants (user_id, vehicle_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                user_id,
                vehicle_id,
            )
        result = await _my_vehicle(conn, user_id, vehicle_id)
    if sum(result["counts"].values()) == 0:
        if get_settings().read_only:
            # Read-only mode unmounts the refresh routes; a user add must not start one either.
            logger.info("read-only: ingest not started (vehicle %s)", vehicle_id)
            return result
        # Queued, not started here: a background worker reads the queue one car at a time.
        await ingest_queue.request(vehicle_id)
    return result


# How many cars a subscriber may take off the plan per calendar month (UTC). Without this a
# plan for 2 cars would read any number of cars, two at a time.
PLAN_REMOVALS = {"monthly": 1, "all_access": 5}


class RemovalLimitReached(Exception):
    """A subscriber has already removed as many plan cars this month as the plan allows."""

    def __init__(self, plan: str, resets_on: date) -> None:
        super().__init__(plan)
        self.plan = plan
        self.limit = PLAN_REMOVALS[plan]
        self.resets_on = resets_on


def _next_month(today: date) -> date:
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


async def _removal_state(conn, user_id: int) -> dict | None:
    """`{plan, limit, used, resets_on}` for a subscriber held to a removal limit, else None.

    None for an admin and for an account without an active subscription: their removals
    are neither counted nor limited.
    """
    row = await conn.fetchrow(
        """
        SELECT
            EXISTS (SELECT 1 FROM users u WHERE u.id = $1 AND u.is_admin) AS admin,
            (SELECT COALESCE(e.plan, 'monthly') FROM entitlements e
              WHERE e.user_id = $1 AND e.kind = 'subscription' AND e.status = 'active'
              ORDER BY (e.plan = 'all_access') DESC LIMIT 1) AS plan,
            (SELECT count(*) FROM vehicle_removals r
              WHERE r.user_id = $1
                AND r.removed_at >= date_trunc('month', now() AT TIME ZONE 'utc')
                                    AT TIME ZONE 'utc') AS used
        """,
        user_id,
    )
    if row["admin"] or row["plan"] not in PLAN_REMOVALS:
        return None
    return {
        "plan": row["plan"],
        "limit": PLAN_REMOVALS[row["plan"]],
        "used": row["used"],
        "resets_on": _next_month(datetime.now(timezone.utc).date()),
    }


async def get_removals(user_id: int) -> dict | None:
    """The caller's removal allowance for GET /api/me, or None when not limited."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _removal_state(conn, user_id)


async def remove_vehicle(user_id: int, vehicle_id: int) -> bool:
    """Unlink the vehicle from the caller only. False when the caller had no such link.

    An unlocked car, and any car on an account without a subscription, comes off freely.
    A car on a subscription counts against the plan's removals for the month and raises
    RemovalLimitReached once they are used.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialise this user's removals so two requests cannot both take the last one.
            await conn.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", user_id)
            linked = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM user_vehicles "
                "WHERE user_id = $1 AND vehicle_id = $2)",
                user_id,
                vehicle_id,
            )
            if not linked:
                return False
            unlocked = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM entitlements WHERE user_id = $1 AND kind = 'unlock' "
                "AND status = 'active' AND vehicle_id = $2)",
                user_id,
                vehicle_id,
            )
            state = None if unlocked else await _removal_state(conn, user_id)
            if state:
                if state["used"] >= state["limit"]:
                    raise RemovalLimitReached(state["plan"], state["resets_on"])
                await conn.execute(
                    "INSERT INTO vehicle_removals (user_id, vehicle_id) VALUES ($1, $2)",
                    user_id,
                    vehicle_id,
                )
            await conn.execute(
                "DELETE FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2",
                user_id,
                vehicle_id,
            )
    return True


async def set_vehicle_alerts(user_id: int, vehicle_id: int, enabled: bool) -> dict | None:
    """Include or leave out one of the caller's cars in alert emails. None when not linked."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_vehicles SET alerts_enabled = $3 WHERE user_id = $1 AND vehicle_id = $2",
            user_id,
            vehicle_id,
            enabled,
        )
        return await _my_vehicle(conn, user_id, vehicle_id)


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


async def _folded_for(
    conn, user_id: int, vehicle_id: int
) -> tuple[str, list[dict], set[str]] | None:
    """`(access, folded rows newest first, readable nhtsa_ids)` for a linked vehicle, else None.

    The one place the sample rule is applied: the readable set is chosen across the
    whole revision-folded list. The bulletin list and the single-bulletin detail both
    read it, so they can never disagree about which rows are locked.
    """
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
    return access, rows, readable


async def my_bulletins(
    user_id: int,
    vehicle_id: int,
    severity: str | None,
    page: int,
    per_page: int,
    search: str | None = None,
    unread_only: bool = False,
    comm_type: str | None = None,
    systems: list[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    nhtsa_id: str | None = None,
) -> dict | None:
    """The caller's folded bulletins for one linked vehicle, or None when it is not linked.

    Under the sample rule the readable set is chosen across the whole folded list,
    before any filter or the page is applied. A search or a systems filter (both read
    document content) only ever matches rows that are readable, so a locked row's
    text or tags cannot leak through which rows match or through the facets. Type
    and date filters match locked rows too: those are identity fields a locked row shows.
    `facets` counts the rows the search and filters select while ignoring `severity`,
    `unread_only` and `nhtsa_id`, so the tabs can show their counts.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        folded = await _folded_for(conn, user_id, vehicle_id)
        if folded is None:
            return None
        access, rows, readable = folded
        sample_counts = dict.fromkeys(SAMPLE_QUOTA, 0)
        for r in rows:
            if r["nhtsa_id"] in readable and r["severity"] in sample_counts:
                sample_counts[r["severity"]] += 1

        selected = rows
        if search or comm_type or systems or date_from or date_to:
            found, _ = await corpus_service.list_communications(
                vehicle_id=vehicle_id,
                status="processed",
                search=search,
                comm_type=comm_type,
                systems=systems,
                date_from=date_from,
                date_to=date_to,
                revisions="latest",
                page=1,
                per_page=_ALL_ROWS,
            )
            matched = {r["nhtsa_id"] for r in found}
            selected = [
                r
                for r in rows
                if r["nhtsa_id"] in matched
                and (not (search or systems) or r["nhtsa_id"] in readable)
            ]

        read_ids = {
            r["nhtsa_id"]
            for r in await conn.fetch(
                "SELECT nhtsa_id FROM bulletin_reads "
                "WHERE user_id = $1 AND nhtsa_id = ANY($2::text[])",
                user_id,
                [r["nhtsa_id"] for r in selected],
            )
        }
        facets = {
            "by_severity": dict.fromkeys(SAMPLE_QUOTA, 0),
            "unread_by_severity": dict.fromkeys(SAMPLE_QUOTA, 0),
        }
        for r in selected:
            if r["severity"] in SAMPLE_QUOTA:
                facets["by_severity"][r["severity"]] += 1
                if r["nhtsa_id"] not in read_ids:
                    facets["unread_by_severity"][r["severity"]] += 1

        shown = [
            r
            for r in selected
            if (severity is None or r["severity"] == severity)
            and (not unread_only or r["nhtsa_id"] not in read_ids)
            and (nhtsa_id is None or r["nhtsa_id"] == nhtsa_id)
        ]
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
            "read": r["nhtsa_id"] in read_ids,
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
        "facets": facets,
    }


async def my_bulletin_detail(user_id: int, vehicle_id: int, nhtsa_id: str) -> dict | None:
    """One bulletin of a linked vehicle under the sample rule.

    None when the vehicle is not linked to the caller or the id is not a current
    bulletin of it. A readable row returns the corpus detail plus `locked: False`; a
    locked row returns its identity fields and `locked: True` and nothing else.

    A reader with full access may also open any other communication linked to the
    vehicle (a superseded revision, or one still pending or failed): symptom search
    resolves a match to the newest revision of its family across every status, which
    the processed-only fold does not contain. Under the sample rule only the fold counts.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        folded = await _folded_for(conn, user_id, vehicle_id)
        if folded is None:
            return None
        access, rows, readable = folded
        row = next((r for r in rows if r["nhtsa_id"] == nhtsa_id), None)
        if row is None and not (
            access == "full"
            and await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM communication_vehicles cv "
                "JOIN communications c ON c.id = cv.communication_id "
                "WHERE cv.vehicle_id = $1 AND c.nhtsa_id = $2)",
                vehicle_id,
                nhtsa_id,
            )
        ):
            return None
    if row is not None and nhtsa_id not in readable:
        return {
            "nhtsa_id": row["nhtsa_id"],
            "communication_type": row["communication_type"],
            "communication_date": row["communication_date"],
            "severity": row["severity"],
            "locked": True,
        }
    detail = await corpus_service.get_communication(nhtsa_id)
    if detail is None:
        return None
    return {**detail, "locked": False}


# The folded, processed families of one vehicle: the latest revision of each stem,
# the same set `public.service.counts_for` counts.
_FAMILIES_SQL = f"""
    SELECT DISTINCT ON (stem) nhtsa_id, severity, communication_date
    FROM (
        SELECT c.nhtsa_id, c.severity, c.communication_date, {REVISION_STEM_SQL} AS stem
        FROM communications c
        JOIN communication_vehicles cv ON cv.communication_id = c.id
        WHERE cv.vehicle_id = $1 AND c.status = 'processed'
    ) c
    ORDER BY stem, communication_date DESC NULLS LAST, nhtsa_id DESC
"""


async def counts_for_user(conn, user_id: int, vehicle_id: int) -> dict:
    """`{unread: {serious, medium, small}, new_this_week}` for one vehicle and one user."""
    rows = await conn.fetch(
        f"""
        SELECT f.severity,
               count(*) FILTER (WHERE br.nhtsa_id IS NULL) AS unread,
               count(*) FILTER (
                   WHERE f.communication_date >= now() - interval '7 days') AS recent
        FROM ({_FAMILIES_SQL}) f
        LEFT JOIN bulletin_reads br ON br.user_id = $2 AND br.nhtsa_id = f.nhtsa_id
        GROUP BY f.severity
        """,
        vehicle_id,
        user_id,
    )
    unread = dict.fromkeys(SAMPLE_QUOTA, 0)
    recent = 0
    for r in rows:
        if r["severity"] in unread:
            unread[r["severity"]] = r["unread"]
        recent += r["recent"]
    return {"unread": unread, "new_this_week": recent}


async def mark_read(user_id: int, nhtsa_id: str) -> bool:
    """Record a read. False unless the bulletin is on one of the caller's linked vehicles."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        linked = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM communications c
                JOIN communication_vehicles cv ON cv.communication_id = c.id
                JOIN user_vehicles uv ON uv.vehicle_id = cv.vehicle_id AND uv.user_id = $1
                WHERE c.nhtsa_id = $2)
            """,
            user_id,
            nhtsa_id,
        )
        if not linked:
            return False
        await conn.execute(
            "INSERT INTO bulletin_reads (user_id, nhtsa_id) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            user_id,
            nhtsa_id,
        )
    return True


async def mark_all_read(user_id: int, vehicle_id: int, severity: str | None) -> int | None:
    """Mark every unread family of a linked vehicle read; the number inserted, or None
    when the vehicle is not linked to the caller."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        linked = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2)",
            user_id,
            vehicle_id,
        )
        if not linked:
            return None
        rows = await conn.fetch(
            f"""
            INSERT INTO bulletin_reads (user_id, nhtsa_id)
            SELECT $2, f.nhtsa_id FROM ({_FAMILIES_SQL}) f
            WHERE ($3::text IS NULL OR f.severity = $3)
            ON CONFLICT DO NOTHING
            RETURNING 1
            """,
            vehicle_id,
            user_id,
            severity,
        )
    return len(rows)
