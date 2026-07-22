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
    """Schema for creating a vehicle to track."""

    vehicle_id: int = Field(..., description="NHTSA Vehicle ID")
    year: str = Field(..., description="Model year (e.g., '2024')")
    model: str = Field(..., description="Model name (e.g., 'SILVERADO EV')")
    make: Optional[str] = Field(None, description="Make, from the NHTSA byYmmt variant")
    trim: Optional[str] = Field(None, description="Trim, from the NHTSA byYmmt variant")
    keywords: list[str] = Field(
        default_factory=list, description="Keywords to filter communications"
    )


class VehicleUpdate(CamelModel):
    """Schema for updating a vehicle.

    make and trim are settable: the router has always forwarded them, so
    without the fields every PATCH raised AttributeError before it reached the
    service.
    """

    year: Optional[str] = None
    model: Optional[str] = None
    make: Optional[str] = None
    trim: Optional[str] = None
    keywords: Optional[list[str]] = None


# --- Output Schemas ---


class VehicleResponse(CamelModel):
    """Schema for vehicle response."""

    id: str = Field(..., description="Row ID in the canonical store")
    vehicle_id: int = Field(..., description="NHTSA Vehicle ID")
    year: str = Field(..., description="Model year")
    model: str = Field(..., description="Model name")
    make: Optional[str] = Field(None, description="Make, as recorded by the pipeline")
    trim: Optional[str] = Field(None, description="Trim, from the NHTSA byYmmt variant")
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
