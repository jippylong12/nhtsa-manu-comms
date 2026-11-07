"""Read API over the cost ledger.

`GET /api/admin/usage` is admin only (JIP-1582).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.auth.deps import admin_user

from src.usage import ledger
from src.usage.schemas import UsageSummary

router = APIRouter(prefix="/admin", tags=["Usage"], dependencies=[Depends(admin_user)])


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
