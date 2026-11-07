"""Pydantic schemas for Vehicles feature."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase for JSON output."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    """Base model with camelCase JSON output."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# --- Input Schemas ---


class VehicleCreate(CamelModel):
    """Schema for creating a vehicle to track.

    Identity is (year, make, model). Unknown fields (a legacy `vehicleId`, or any
    other extra key) are ignored.
    """

    year: str = Field(..., description="Model year (e.g., '2024')")
    make: str = Field(..., description="Make (e.g., 'Ford'); stored uppercase")
    model: str = Field(..., description="Model nameplate (e.g., 'F-150'); stored canonical ('F 150')")
    keywords: list[str] = Field(
        default_factory=list, description="Keywords to filter communications"
    )


class VehicleUpdate(CamelModel):
    """Schema for updating a vehicle.

    Only keywords are editable. Year, make and model are the vehicle's identity;
    changing them means deleting and re-adding.
    """

    keywords: Optional[list[str]] = None


# --- Output Schemas ---


class VehicleResponse(CamelModel):
    """Schema for vehicle response."""

    id: str = Field(..., description="Row ID in the canonical store")
    vehicle_id: int = Field(..., description="Vehicle id (the row id; same value as `id`)")
    year: str = Field(..., description="Model year")
    model: str = Field(..., description="Model name")
    make: Optional[str] = Field(None, description="Make, as recorded by the pipeline")
    keywords: list[str] = Field(default_factory=list, description="Filter keywords")
    last_fetched: Optional[datetime] = Field(None, description="Last time NHTSA was queried")
    comm_count: int = Field(
        0, description="Communications in the corpus for this vehicle, derived on read"
    )
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record last update timestamp")


class VehicleListResponse(CamelModel):
    """Schema for paginated vehicle list."""

    items: list[VehicleResponse]
    total: int
    page: int
    per_page: int
