"""Schemas for /api/billing."""

from typing import Literal

from src.vehicles.schemas import CamelModel


class CheckoutRequest(CamelModel):
    kind: Literal["unlock", "subscription"]
    # Which subscription; ignored for an unlock.
    plan: Literal["monthly", "all_access"] = "monthly"
    vehicle_id: int | None = None


class UrlResponse(CamelModel):
    url: str
