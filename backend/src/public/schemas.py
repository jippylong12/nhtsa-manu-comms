"""Schemas for GET /api/public/lookup."""

from datetime import datetime
from typing import Literal

from src.vehicles.schemas import CamelModel


class SeverityCounts(CamelModel):
    serious: int = 0
    medium: int = 0
    small: int = 0


class LookupExample(CamelModel):
    vehicle_id: int
    year: int
    make: str
    model: str
    counts: SeverityCounts
    latest_date: datetime | None = None


class LookupResponse(CamelModel):
    vehicle_id: int | None = None
    year: int
    make: str
    model: str
    availability: Literal["ready", "processing", "unknown"]
    counts: SeverityCounts
    latest_date: datetime | None = None
    example: LookupExample | None = None
