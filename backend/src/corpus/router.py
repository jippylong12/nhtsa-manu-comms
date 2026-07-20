"""FastAPI router for the Postgres corpus read API.

Mounted at /api/corpus so it lives alongside the Mongo-backed
/api/communications endpoints without displacing them.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.corpus import service
from src.corpus.schemas import (
    CommunicationDetail,
    CommunicationListResponse,
    CommunicationSummary,
    TagVocabulary,
)

router = APIRouter(prefix="/corpus", tags=["Corpus"])


def _csv(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


@router.get(
    "/communications",
    response_model=CommunicationListResponse,
    summary="List processed communications with filters and full-text search",
)
async def list_communications(
    vehicle_id: Optional[int] = Query(None, description="NHTSA vehicleId"),
    comm_type: Optional[str] = Query(None, description="Communication type code (TSB, PIT, ...)"),
    status: Optional[str] = Query(None, description="pending | processed | failed"),
    date_from: Optional[datetime] = Query(None, description="Earliest communication date"),
    date_to: Optional[datetime] = Query(None, description="Latest communication date"),
    systems: Optional[str] = Query(None, description="Comma-separated system tags (any match)"),
    components: Optional[str] = Query(
        None, description="Comma-separated component tags (any match)"
    ),
    search: Optional[str] = Query(
        None, description="Full-text query over summaries and document text"
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> CommunicationListResponse:
    if status and status not in ("pending", "processed", "failed"):
        raise HTTPException(status_code=400, detail="status must be pending, processed, or failed")

    items, total = await service.list_communications(
        vehicle_id=vehicle_id,
        comm_type=comm_type,
        status=status,
        date_from=date_from,
        date_to=date_to,
        systems=_csv(systems),
        components=_csv(components),
        search=search,
        page=page,
        per_page=per_page,
    )
    return CommunicationListResponse(
        items=[CommunicationSummary(**i) for i in items],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/tags",
    response_model=TagVocabulary,
    summary="Distinct system and component tags with counts",
)
async def tag_vocabulary(limit: int = Query(100, ge=1, le=500)) -> TagVocabulary:
    vocab = await service.tag_vocabulary(limit=limit)
    return TagVocabulary(**vocab)


@router.get(
    "/communications/{nhtsa_id}",
    response_model=CommunicationDetail,
    summary="Get one processed communication with its documents",
)
async def get_communication(nhtsa_id: str) -> CommunicationDetail:
    comm = await service.get_communication(nhtsa_id)
    if comm is None:
        raise HTTPException(status_code=404, detail="Communication not found")
    return CommunicationDetail(**comm)
