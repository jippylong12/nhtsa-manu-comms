"""Read API over the cost ledger.

`GET /api/admin/usage` is unauthenticated for now because the deployment is
single-tenant, but it is namespaced under /admin so Phase C can put the whole
prefix behind auth in one place rather than hunting for endpoints.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from src.usage import ledger
from src.usage.schemas import UsageSummary

router = APIRouter(prefix="/admin", tags=["Usage"])


@router.get(
    "/usage",
    response_model=UsageSummary,
    summary="Aggregate model spend with breakdowns by kind, model, and day",
)
async def usage_summary(
    date_from: Optional[datetime] = Query(None, description="Inclusive lower bound"),
    date_to: Optional[datetime] = Query(None, description="Exclusive upper bound"),
) -> UsageSummary:
    return UsageSummary(**await ledger.summarise(date_from, date_to))
