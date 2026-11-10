"""Who may read a vehicle's bulletins in full."""

from typing import Literal

import asyncpg


# How many cars each subscription covers at a time. A car the account has unlocked
# ($3, forever) is its own purchase and does not take one of these places.
PLAN_VEHICLES = {"monthly": 2, "all_access": 20}


async def access_for(
    conn: asyncpg.Connection, user_id: int, vehicle_id: int
) -> Literal["full", "sample"]:
    """`full` for an admin, an active unlock of this vehicle, or a car an active plan covers.

    A plan covers the account's first N cars that are not unlocked, oldest link first
    (N from PLAN_VEHICLES). A car linked past that, which happens when a subscriber moves
    to a smaller plan, reads as `sample` until a place frees up or the car is unlocked.

    Scoped to `user_id` only: another account's entitlement never counts. A
    `past_due` or `canceled` row never grants anything. Everyone else gets `sample`.
    """
    full = await conn.fetchval(
        """
        WITH plan AS (
            SELECT COALESCE(e.plan, 'monthly') AS plan FROM entitlements e
            WHERE e.user_id = $1 AND e.status = 'active' AND e.kind = 'subscription'
            ORDER BY (e.plan = 'all_access') DESC LIMIT 1
        ),
        over_limit AS (
            -- Linked plan cars past the plan's count, oldest link first.
            SELECT uv.vehicle_id FROM user_vehicles uv
            WHERE uv.user_id = $1 AND NOT EXISTS (
                SELECT 1 FROM entitlements e
                WHERE e.user_id = $1 AND e.kind = 'unlock' AND e.status = 'active'
                  AND e.vehicle_id = uv.vehicle_id)
            ORDER BY uv.created_at, uv.vehicle_id
            OFFSET (SELECT CASE plan WHEN 'all_access' THEN $4::int ELSE $3::int END FROM plan)
        )
        SELECT EXISTS (SELECT 1 FROM users u WHERE u.id = $1 AND u.is_admin)
            OR EXISTS (
                SELECT 1 FROM entitlements e
                WHERE e.user_id = $1 AND e.status = 'active'
                  AND e.kind = 'unlock' AND e.vehicle_id = $2)
            OR (EXISTS (SELECT 1 FROM plan)
                AND NOT EXISTS (SELECT 1 FROM over_limit o WHERE o.vehicle_id = $2))
        """,
        user_id,
        vehicle_id,
        PLAN_VEHICLES["monthly"],
        PLAN_VEHICLES["all_access"],
    )
    return "full" if full else "sample"


async def list_for_user(conn: asyncpg.Connection, user_id: int) -> list[dict]:
    """The user's entitlements, oldest first."""
    rows = await conn.fetch(
        "SELECT kind, plan, vehicle_id, status, current_period_end FROM entitlements "
        "WHERE user_id = $1 ORDER BY id",
        user_id,
    )
    return [dict(r) for r in rows]


async def customer_id_for(conn: asyncpg.Connection, user_id: int) -> str | None:
    """The Stripe customer id on any of the user's entitlements, newest first, or None."""
    return await conn.fetchval(
        "SELECT stripe_customer_id FROM entitlements "
        "WHERE user_id = $1 AND stripe_customer_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        user_id,
    )
