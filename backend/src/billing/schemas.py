"""Schemas for /api/billing."""

from typing import Literal

from src.vehicles.schemas import CamelModel


class CheckoutRequest(CamelModel):
    kind: Literal["unlock", "subscription"]
    # Which subscription; ignored for an unlock.
    plan: Literal["monthly", "all_access"] = "monthly"
    vehicle_id: int | None = None


class PortalRequest(CamelModel):
    # `cancel` opens the portal straight on the cancel page for the caller's subscription.
    flow: Literal["manage", "cancel"] = "manage"


class UrlResponse(CamelModel):
    url: str
