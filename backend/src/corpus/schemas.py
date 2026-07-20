"""Response schemas for the Postgres corpus read API.

Reuses the existing `CamelModel` base so JSON stays camelCase, matching the rest
of the API and the frontend's expectations.
"""

from datetime import datetime
from typing import Optional

from pydantic import Field

from src.vehicles.schemas import CamelModel


class CorpusDocument(CamelModel):
    """A processed PDF: extraction metadata plus the LLM structured fields."""

    id: int
    url: str
    doc_summary: Optional[str] = None
    extraction_method: Optional[str] = None
    page_count: Optional[int] = None
    llm_summary: Optional[str] = None
    doc_kind: Optional[str] = None
    symptoms: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    remedy: Optional[str] = None
    applicability: Optional[str] = None
    has_embedding: bool = False


class CorpusVehicle(CamelModel):
    """A vehicle a communication is linked to."""

    nhtsa_vehicle_id: int
    year: int
    make: str
    model: str
    trim: Optional[str] = None


class CommunicationSummary(CamelModel):
    """List-view row: the communication plus a rolled-up view of its documents."""

    nhtsa_id: str
    communication_number: Optional[str] = None
    communication_type: Optional[str] = None
    communication_date: Optional[datetime] = None
    summary: str = ""
    status: str
    document_count: int = 0
    # Rolled up across the communication's documents so the list view can show
    # tags without a second request per row.
    llm_summary: Optional[str] = None
    symptoms: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    vehicles: list[CorpusVehicle] = Field(default_factory=list)


class CommunicationDetail(CamelModel):
    """Detail-view: the communication with its full document list."""

    nhtsa_id: str
    communication_number: Optional[str] = None
    communication_type: Optional[str] = None
    communication_date: Optional[datetime] = None
    summary: str = ""
    details_summary: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    processed_at: Optional[datetime] = None
    documents: list[CorpusDocument] = Field(default_factory=list)
    vehicles: list[CorpusVehicle] = Field(default_factory=list)


class CommunicationListResponse(CamelModel):
    """Paginated list envelope, matching the Mongo endpoint's shape."""

    items: list[CommunicationSummary]
    total: int
    page: int
    per_page: int


class TagCount(CamelModel):
    """One tag and how many documents carry it, for filter UIs."""

    tag: str
    count: int


class TagVocabulary(CamelModel):
    """Distinct systems and components with counts."""

    systems: list[TagCount]
    components: list[TagCount]
