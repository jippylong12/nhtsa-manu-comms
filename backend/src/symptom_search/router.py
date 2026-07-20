"""FastAPI router for the symptom-search API.

POST /api/symptom-search : { vehicleId, description } -> grounded answer + matches.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

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


@router.post(
    "/symptom-search",
    response_model=SymptomSearchResponse,
    summary="Match a described symptom to this vehicle's manufacturer communications",
)
async def symptom_search(payload: SymptomSearchRequest, request: Request) -> SymptomSearchResponse:
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

    result = await service.search(payload.vehicle_id, description)
    return SymptomSearchResponse(**result)
