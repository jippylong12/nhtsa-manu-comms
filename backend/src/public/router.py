"""/api/public: lookup and the unsubscribe link, both unauthenticated."""

from fastapi import APIRouter, HTTPException, Query, Response

from src.public import service
from src.public.schemas import LookupResponse, UnsubscribeResponse

router = APIRouter(prefix="/public", tags=["Public"])


@router.get("/lookup", response_model=LookupResponse, summary="Severity counts for a vehicle")
async def lookup(
    year: str = Query(...), make: str = Query(...), model: str = Query(...)
) -> LookupResponse:
    """Counts of revision families by severity; `unknown` carries an example vehicle."""
    try:
        year_int = int(year.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="year must be a number") from None
    return LookupResponse(**await service.lookup(year_int, make, model))


@router.get(
    "/unsubscribe/{token}",
    response_model=UnsubscribeResponse,
    summary="Unsubscribe from daily alerts (the link in every alert email)",
)
async def unsubscribe(token: str, response: Response) -> UnsubscribeResponse:
    """Idempotent. The body never says whose token it was; unknown and malformed are one 404."""
    response.headers["Cache-Control"] = "no-store"
    result = await service.unsubscribe(token)
    if result is None:
        raise HTTPException(
            status_code=404, detail="not found", headers={"Cache-Control": "no-store"}
        )
    return UnsubscribeResponse(status=result)
