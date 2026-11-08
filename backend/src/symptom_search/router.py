"""FastAPI router for the symptom-search API.

POST /api/symptom-search : { vehicleId, description } -> grounded answer + matches.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.deps import current_user
from src.billing.entitlements import access_for
from src.db import get_pool
from src.symptom_search import service
from src.symptom_search.ratelimit import SlidingWindowLimiter
from src.symptom_search.schemas import SymptomSearchRequest, SymptomSearchResponse

log = logging.getLogger("symptom_search.router")

router = APIRouter(tags=["Symptom Search"])

# Personal-use rate limit: each query spends an embedding + an LLM call, so this
# caps accidental runaway rather than defending against abuse.
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0
_limiter = SlidingWindowLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_SECONDS)

# Per-user daily cap, counted from the usage ledger (one answer_compose row per query).
DAILY_QUERY_LIMIT = 30


async def _gate(user: dict, vehicle_id: int) -> None:
    """Raise 404/402/429 unless this user may run one more search on this vehicle."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        linked = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2)",
            user["id"],
            vehicle_id,
        )
        if not linked:
            raise HTTPException(status_code=404, detail=f"Vehicle {vehicle_id} is not tracked")
        if await access_for(conn, user["id"], vehicle_id) != "full":
            raise HTTPException(
                status_code=402, detail="unlock this vehicle or subscribe to search it"
            )
        used = await conn.fetchval(
            """
            SELECT count(*) FROM usage_events
            WHERE kind = 'answer_compose' AND meta->>'user_id' = $1
              AND occurred_at > now() - interval '24 hours'
            """,
            str(user["id"]),
        )
        if used >= DAILY_QUERY_LIMIT:
            raise HTTPException(status_code=429, detail="daily search limit reached")


@router.post(
    "/symptom-search",
    response_model=SymptomSearchResponse,
    summary="Match a described symptom to this vehicle's manufacturer communications",
    responses={
        402: {"description": "unlock this vehicle or subscribe to search it"},
        429: {"description": "daily search limit reached (30 per user per 24 hours)"},
    },
)
async def symptom_search(
    payload: SymptomSearchRequest,
    request: Request,
    user: dict = Depends(current_user),
) -> SymptomSearchResponse:
    key = request.client.host if request.client else "anonymous"
    if not _limiter.allow(key):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(_limiter.retry_after(key))},
        )

    description = payload.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description must not be empty")

    if not await service.vehicle_exists(payload.vehicle_id):
        raise HTTPException(status_code=404, detail=f"Vehicle {payload.vehicle_id} is not tracked")

    if not user["is_admin"]:
        await _gate(user, payload.vehicle_id)

    result = await service.search(payload.vehicle_id, description, user_id=user["id"])
    return SymptomSearchResponse(**result)
