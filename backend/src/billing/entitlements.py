"""Who may read a vehicle's bulletins in full."""

from typing import Literal

import asyncpg


async def access_for(
    conn: asyncpg.Connection, user_id: int, vehicle_id: int
) -> Literal["full", "sample"]:
    """`full` for an admin, an active subscription, or an active unlock of this vehicle.

    Scoped to `user_id` only: another account's entitlement never counts. A
    `past_due` or `canceled` row never grants anything. Everyone else gets `sample`.
    """
    full = await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM users u WHERE u.id = $1 AND u.is_admin)
            OR EXISTS (
                SELECT 1 FROM entitlements e
                WHERE e.user_id = $1 AND e.status = 'active'
                  AND (e.kind = 'subscription'
                       OR (e.kind = 'unlock' AND e.vehicle_id = $2))
            )
        """,
        user_id,
        vehicle_id,
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
