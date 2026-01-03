"""Pydantic schemas for Communications feature."""

from datetime import datetime
from typing import Optional
from pydantic import Field

from src.vehicles.schemas import CamelModel


# --- Communication Types ---
# Extended categorization based on:
# 1. Manufacturer communication number prefix (PIT, PIC, PIP, TSB)
# 2. Summary text analysis for bulletins/programs
# 3. Communication number patterns (XX-NA-XXX format)

COMM_TYPE_MAP = {
    # Prefix-based types
    "TSB": "Technical Service Bulletin",
    "PIT": "Preliminary Info Technical",
    "PIC": "Preliminary Info Customer",
    "PIP": "Preliminary Info Parts",
    # Summary-based types (detected from text)
    "SB": "Service Bulletin",
    "TB": "Technical Bulletin",
    "IB": "Informational Bulletin",
    "SU": "Service Update",
    "WA": "Warranty Administration",
    "CSP": "Customer Satisfaction Program",
    "RC": "Recall/Campaign",
    "SC": "Special Coverage",
    # Format-based types (catchall before OTHER)
    "NA": "NA Bulletin",  # XX-NA-XXX format
    # Default
    "OTHER": "Other",
}


def get_comm_type_from_prefix(comm_number: Optional[str]) -> Optional[str]:
    """Extract communication type from manufacturer communication number prefix."""
    if not comm_number:
        return None
    prefix = comm_number[:3].upper()
    if prefix in ("TSB", "PIT", "PIC", "PIP"):
        return prefix
    # NA format check moved to get_comm_type as fallback
    return None


def get_comm_type_from_summary(summary: Optional[str]) -> Optional[str]:
    """Extract communication type by analyzing the summary text."""
    if not summary:
        return None
    
    text = summary.lower()
    
    # Order matters - check more specific patterns first
    if "customer satisfaction" in text:
        return "CSP"
    if "special coverage" in text:
        return "SC"
    if "safety recall" in text or "recall campaign" in text:
        return "RC"
    if "warranty administration" in text:
        return "WA"
    # Check for bulletin type patterns with "provides" language
    if "technical service bulletin" in text or "this technical bulletin provides" in text:
        return "TSB"
    if "technical bulletin" in text:
        return "TB"
    if "this service bulletin provides" in text or "service bulletin" in text:
        return "SB"
    if "service update" in text:
        return "SU"
    if "informational bulletin" in text or "this bulletin provides information" in text:
        return "IB"
    if "preliminary information" in text:
        return "PIT"
    
    return None


def is_na_format(comm_number: Optional[str]) -> bool:
    """Check if communication number follows XX-NA-XXX format."""
    if not comm_number:
        return False
    upper = comm_number.upper()
    return "-NA-" in upper or upper.endswith("-NA")


def get_comm_type(comm_number: Optional[str], summary: Optional[str] = None) -> str:
    """
    Determine communication type using multiple detection methods.
    Priority: 1) Prefix-based, 2) Summary-based, 3) NA format, 4) OTHER
    """
    # First try prefix-based detection (TSB, PIT, PIC, PIP)
    prefix_type = get_comm_type_from_prefix(comm_number)
    if prefix_type:
        return prefix_type
    
    # Then try summary-based detection
    if summary:
        summary_type = get_comm_type_from_summary(summary)
        if summary_type:
            return summary_type
    
    # NA format is the last catchall before OTHER
    if is_na_format(comm_number):
        return "NA"
    
    return "OTHER"



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
    communication_type: str = Field("OTHER", description="Communication type (TSB, PIT, PIC, PIP, OTHER)")
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


class CategoryStats(CamelModel):
    """Stats for a single category."""

    type: str = Field(..., description="Category type code")
    label: str = Field(..., description="Human readable label")
    count: int = Field(..., description="Number of communications")


class VehicleStats(CamelModel):
    """Statistics for a vehicle's communications."""

    vehicle_id: int
    total_count: int = Field(..., description="Total communications")
    last_30_days_count: int = Field(..., description="Communications in last 30 days")
    categories: list[CategoryStats] = Field(default_factory=list, description="Breakdown by type")


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


class VehicleVariant(CamelModel):
    """Schema for vehicle variant from discovery API."""

    vehicle_id: int = Field(..., alias="VehicleId", description="Safety Ratings Vehicle ID")
    vehicle_description: str = Field(..., alias="VehicleDescription", description="Vehicle description")


