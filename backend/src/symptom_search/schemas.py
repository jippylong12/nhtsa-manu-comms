"""Request/response schemas for the symptom-search API.

Reuses the shared ``CamelModel`` so JSON stays camelCase like the rest of the API.
"""

from datetime import datetime
from typing import Optional

from pydantic import Field

from src.vehicles.schemas import CamelModel


class SymptomSearchRequest(CamelModel):
    """A described problem plus the vehicle it is about."""

    vehicle_id: int = Field(..., description="Vehicle id to scope the search to")
    description: str = Field(..., min_length=1, description="Plain-language symptom description")


class SymptomMatch(CamelModel):
    """One retrieved communication offered as a possible match."""

    nhtsa_id: str
    communication_number: Optional[str] = None
    communication_type: Optional[str] = None
    communication_date: Optional[datetime] = None
    llm_summary: Optional[str] = None
    symptoms: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    remedy: Optional[str] = None
    relevance_score: float = 0.0
    cosine_sim: Optional[float] = None
    # True when the composed answer cited this communication.
    cited: bool = False
    # Older revisions of this same bulletin that were folded into this result.
    superseded_count: int = 0


class QueryCostOut(CamelModel):
    """Per-query cost breakdown, surfaced so it can be shown and tracked."""

    embed_tokens: int = 0
    embed_cost_usd: float = 0.0
    llm_model: str = ""
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_cost_usd: float = 0.0
    total_usd: float = 0.0


class SymptomSearchResponse(CamelModel):
    """The grounded answer plus its supporting matches and the query's cost."""

    answer: str
    refused: bool = False
    confidence: float = 0.0
    model: str = ""
    # False when retrieval succeeded but answer composition could not run.
    answer_generated: bool = True
    matches: list[SymptomMatch] = Field(default_factory=list)
    cited_ids: list[str] = Field(default_factory=list)
    cost: QueryCostOut
