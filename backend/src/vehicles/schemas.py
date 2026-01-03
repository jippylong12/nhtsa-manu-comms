"""Pydantic schemas for Vehicles feature."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


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
    keywords: list[str] = Field(
        default_factory=list, description="Keywords to filter communications"
    )


class VehicleUpdate(CamelModel):
    """Schema for updating a vehicle."""

    year: Optional[str] = None
    model: Optional[str] = None
    keywords: Optional[list[str]] = None


# --- Output Schemas ---


class VehicleResponse(CamelModel):
    """Schema for vehicle response."""

    id: str = Field(..., alias="_id", description="MongoDB document ID")
    vehicle_id: int = Field(..., description="NHTSA Vehicle ID")
    year: str = Field(..., description="Model year")
    model: str = Field(..., description="Model name")
    keywords: list[str] = Field(default_factory=list, description="Filter keywords")
    last_fetched: Optional[datetime] = Field(None, description="Last API fetch time")
    comm_count: int = Field(0, description="Number of communications found")
    created_at: datetime = Field(..., description="Record creation timestamp")
    updated_at: datetime = Field(..., description="Record last update timestamp")


class VehicleListResponse(CamelModel):
    """Schema for paginated vehicle list."""

    items: list[VehicleResponse]
    total: int
    page: int
    per_page: int
