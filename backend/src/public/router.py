"""GET /api/public/lookup: unauthenticated, read-only."""

from fastapi import APIRouter, HTTPException, Query

from src.public import service
from src.public.schemas import LookupResponse

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
