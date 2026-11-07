"""Business logic for the signed-in user's profile."""

from src.db import get_pool


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
