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
