"""Pydantic schemas for the in-app refresh."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from src.vehicles.schemas import CamelModel


class RefreshPreview(CamelModel):
    """What a refresh would do. Costs nothing to ask."""

    vehicle_id: int
    listed_by_nhtsa: int = Field(..., description="Communications NHTSA lists for this vehicle")
    new_bulletins: int = Field(..., description="Listed but not yet in the corpus")
    unfinished_documents: int = Field(
        ..., description="Already in the corpus for this vehicle but never analysed"
    )
    documents_to_process: int = Field(..., description="Total billable documents expected")
    up_to_date: bool = Field(..., description="True when there is nothing to do")

    estimated_cost_low_usd: float
    estimated_cost_high_usd: float
    estimated_cost_text: str = Field(..., description="Ready to show, e.g. 'about $0.03'")
    estimated_seconds: int
    estimated_duration_text: str = Field(..., description="Ready to show, e.g. 'around 4 minutes'")
    cost_per_document_usd: float = Field(
        ..., description="Derived from the usage ledger, not hardcoded"
    )


class RefreshRun(CamelModel):
    """A refresh in flight, or the last one that ran."""

    id: int
    vehicle_id: int
    status: str = Field(..., description="queued, running, succeeded or failed")
    stage: Optional[str] = Field(None, description="sync, read, analyse, index or finalise")
    stage_note: Optional[str] = Field(None, description="A sentence for the user")
    done: int
    total: int

    expected_documents: int
    estimated_cost_usd: float
    actual_cost_usd: Optional[float] = Field(
        None, description="Metered spend for this run, set when it finishes"
    )

    counts: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(
        None, description="Why it failed, or what it could not finish on a partial success"
    )

    started_at: datetime
    heartbeat_at: datetime
    finished_at: Optional[datetime] = None
