"""Schemas for GET /api/me."""

from datetime import datetime

from src.vehicles.schemas import CamelModel


class EntitlementResponse(CamelModel):
    kind: str
    vehicle_id: int | None = None
    status: str
    current_period_end: datetime | None = None


class MeResponse(CamelModel):
    id: int
    email: str
    display_name: str | None = None
    is_admin: bool
    entitlements: list[EntitlementResponse]
    vehicles: list[dict]
