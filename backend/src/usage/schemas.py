"""Response schemas for the usage/cost ledger API."""

from datetime import datetime
from typing import Optional

from pydantic import Field

from src.vehicles.schemas import CamelModel


class UsageTotals(CamelModel):
    events: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class UsageByKind(UsageTotals):
    kind: str


class UsageByModel(UsageTotals):
    model: str


class UsageByDay(UsageTotals):
    day: datetime


class UsageSummary(CamelModel):
    """Aggregate spend plus breakdowns that reconcile with it."""

    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    totals: UsageTotals
    by_kind: list[UsageByKind] = Field(default_factory=list)
    by_model: list[UsageByModel] = Field(default_factory=list)
    by_day: list[UsageByDay] = Field(default_factory=list)
