"""Pydantic schemas for Communications feature."""

from datetime import datetime
from typing import Optional
from pydantic import Field

from src.vehicles.schemas import CamelModel


# --- Embedded Schemas ---


class AssociatedProduct(CamelModel):
    """Product associated with a communication."""

    product_year: str = Field(..., description="Model year")
    product_model: str = Field(..., description="Model name")
    product_make: Optional[str] = Field(None, description="Make/manufacturer")


class AssociatedDocument(CamelModel):
    """Document associated with a communication."""

    url: str = Field(..., description="Document URL")
    summary: str = Field(..., description="Document summary/title")
    load_date: Optional[str] = Field(None, description="When the document was loaded")


# --- Output Schemas ---


class CommunicationResponse(CamelModel):
    """Schema for a manufacturer communication."""

    id: str = Field(..., alias="_id", description="MongoDB document ID")
    nhtsa_id: int = Field(..., description="NHTSA ID number")
    vehicle_id: int = Field(..., description="Associated vehicle ID")
    communication_number: Optional[str] = Field(None, description="Manufacturer comm number")
    communication_date: Optional[str] = Field(None, description="Communication date")
    summary: str = Field("", description="Communication summary")
    details_summary: Optional[str] = Field(None, description="Summary from vehicle details")
    associated_products: list[AssociatedProduct] = Field(
        default_factory=list, description="Associated vehicle products"
    )
    associated_documents: list[AssociatedDocument] = Field(
        default_factory=list, description="Associated document links"
    )
    matched_keywords: list[str] = Field(
        default_factory=list, description="Keywords that matched this communication"
    )
    fetched_at: datetime = Field(..., description="When this was fetched from NHTSA")


class CommunicationListResponse(CamelModel):
    """Schema for paginated communication list."""

    items: list[CommunicationResponse]
    total: int
    page: int
    per_page: int


class SearchFilters(CamelModel):
    """Schema for search filters."""

    vehicle_id: Optional[int] = None
    year: Optional[str] = None
    model: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)


class FetchRequest(CamelModel):
    """Request to trigger a fresh fetch from NHTSA."""

    vehicle_id: int = Field(..., description="NHTSA Vehicle ID to fetch communications for")
    force_refresh: bool = Field(False, description="Force refresh even if recently fetched")


class FetchProgress(CamelModel):
    """Progress update during fetch operation."""

    status: str = Field(..., description="Current status (pending, fetching, complete, error)")
    progress: int = Field(0, description="Progress percentage (0-100)")
    message: str = Field("", description="Status message")
    total_ids: int = Field(0, description="Total communication IDs to fetch")
    fetched_ids: int = Field(0, description="Number of IDs fetched so far")
    new_count: int = Field(0, description="New communications found")


class FetchResult(CamelModel):
    """Result of a fetch operation."""

    vehicle_id: int
    total_fetched: int = Field(..., description="Total communications fetched")
    new_count: int = Field(..., description="New communications stored")
    matched_count: int = Field(..., description="Communications matching filters")
    duration_seconds: float = Field(..., description="Fetch duration in seconds")
