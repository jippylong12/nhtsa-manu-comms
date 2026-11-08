"""Schemas for /api/me."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from src.public.schemas import SeverityCounts
from src.vehicles.schemas import CamelModel


class EntitlementResponse(CamelModel):
    kind: str
    vehicle_id: int | None = None
    status: str
    current_period_end: datetime | None = None


class MyVehicle(CamelModel):
    vehicle_id: int
    year: int
    make: str
    model: str
    availability: Literal["ready", "processing"]
    counts: SeverityCounts
    access: Literal["full", "sample"]
    added_at: datetime


class AlertsStatus(CamelModel):
    enabled: bool
    last_sent_at: datetime | None = None
    eligible: bool


class AlertsUpdate(CamelModel):
    enabled: bool


class MeResponse(CamelModel):
    id: int
    email: str
    display_name: str | None = None
    is_admin: bool
    entitlements: list[EntitlementResponse]
    vehicles: list[MyVehicle]
    alerts: AlertsStatus


class MyVehicleCreate(CamelModel):
    """A vehicle identity to add. There is deliberately no user field: the caller is the token."""

    year: int
    make: str = Field(min_length=1)
    model: str = Field(min_length=1)


class BulletinDocument(CamelModel):
    url: str


class MyBulletin(CamelModel):
    """One folded bulletin. A locked row carries only the five identity fields."""

    nhtsa_id: str
    communication_number: str | None = None
    communication_type: str | None = None
    communication_date: datetime | None = None
    severity: str
    superseded_count: int = 0
    locked: bool
    summary: str | None = None
    llm_summary: str | None = None
    symptoms: list[str] | None = None
    systems: list[str] | None = None
    components: list[str] | None = None
    remedy: str | None = None
    applicability: str | None = None
    applicability_years: list[int] | None = None
    applicability_models: list[str] | None = None
    applicability_conditions: str | None = None
    audience: str | None = None
    documents: list[BulletinDocument] | None = None


class MyBulletinsResponse(CamelModel):
    items: list[MyBulletin]
    total: int
    page: int
    per_page: int
    access: Literal["full", "sample"]
    sample_counts: SeverityCounts
