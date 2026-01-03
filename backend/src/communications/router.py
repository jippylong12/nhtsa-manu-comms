"""API router for Communications feature."""

import json
from fastapi import APIRouter, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from typing import Optional, Any

from src.communications.schemas import (
    CommunicationResponse,
    CommunicationListResponse,
    FetchRequest,
    FetchResult,
    VehicleStats,
)
from src.communications.service import CommunicationService


router = APIRouter(prefix="/communications", tags=["Communications"])


@router.get(
    "",
    response_model=CommunicationListResponse,
    summary="List manufacturer communications",
    response_description="Paginated list of communications",
)
async def list_communications(
    vehicle_id: Optional[int] = Query(None, description="Filter by vehicle ID"),
    year: Optional[str] = Query(None, description="Filter by model year"),
    model: Optional[str] = Query(None, description="Filter by model name"),
    keywords: Optional[str] = Query(None, description="Comma-separated keywords to filter by"),
    search: Optional[str] = Query(None, description="Search in summary and comm number"),
    comm_type: Optional[str] = Query(None, description="Filter by type (TSB, PIT, PIC, PIP, OTHER)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> CommunicationListResponse:
    """Get a paginated list of manufacturer communications with optional filters."""
    kw_list = [k.strip() for k in keywords.split(",")] if keywords else None

    comms, total = await CommunicationService.list_communications(
        vehicle_id=vehicle_id,
        year=year,
        model=model,
        keywords=kw_list,
        search=search,
        comm_type=comm_type,
        page=page,
        per_page=per_page,
    )

    return CommunicationListResponse(
        items=[CommunicationResponse(**c) for c in comms],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/{nhtsa_id}",
    response_model=CommunicationResponse,
    summary="Get a communication by NHTSA ID",
    response_description="The requested communication",
)
async def get_communication(nhtsa_id: int) -> CommunicationResponse:
    """Get a single manufacturer communication by its NHTSA ID."""
    comm = await CommunicationService.get_communication(nhtsa_id)
    if not comm:
        raise HTTPException(status_code=404, detail="Communication not found")
    return CommunicationResponse(**comm)


@router.post(
    "/fetch",
    summary="Fetch communications from NHTSA (SSE)",
    response_description="Server-sent events stream with progress updates",
)
async def fetch_communications(payload: FetchRequest):
    """
    Trigger a fresh fetch of communications from NHTSA API.

    Returns a Server-Sent Events stream with progress updates.
    """

    async def event_generator():
        async for progress in CommunicationService.fetch_and_store(
            vehicle_id=payload.vehicle_id,
            force_refresh=payload.force_refresh,
        ):
            yield f"data: {json.dumps(progress)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/fetch-sync",
    response_model=FetchResult,
    summary="Fetch communications from NHTSA (synchronous)",
    response_description="Fetch result summary",
)
async def fetch_communications_sync(payload: FetchRequest) -> FetchResult:
    """
    Trigger a fresh fetch of communications from NHTSA API.

    This is the synchronous version that waits for completion.
    For real-time progress, use the SSE endpoint instead.
    """
    import time

    start_time = time.time()
    last_progress = None

    async for progress in CommunicationService.fetch_and_store(
        vehicle_id=payload.vehicle_id,
        force_refresh=payload.force_refresh,
    ):
        last_progress = progress

    if not last_progress:
        raise HTTPException(status_code=500, detail="Fetch failed")

    if last_progress["status"] == "error":
        raise HTTPException(status_code=400, detail=last_progress["message"])

    duration = time.time() - start_time
    return FetchResult(
        vehicle_id=payload.vehicle_id,
        total_fetched=last_progress.get("total_ids", 0),
        new_count=last_progress.get("new_count", 0),
        matched_count=last_progress.get("fetched_ids", 0),
        duration_seconds=round(duration, 2),
    )


@router.get(
    "/stats/{vehicle_id}",
    response_model=VehicleStats,
    summary="Get communication stats for a vehicle",
    response_description="Vehicle statistics including category breakdown",
)
async def get_vehicle_stats(vehicle_id: int) -> VehicleStats:
    """Get statistics for a vehicle's communications."""

    stats = await CommunicationService.get_vehicle_stats(vehicle_id)
    return VehicleStats(**stats)


@router.get(
    "/discovery/years",
    response_model=list[int],
    summary="Get model years",
    response_description="List of available model years",
)
async def get_model_years() -> list[int]:
    """Get available model years for vehicle discovery."""
    return await CommunicationService.get_discovery_years()


@router.get(
    "/discovery/makes",
    response_model=list[str],
    summary="Get makes for a model year",
    response_description="List of available makes",
)
async def get_makes(
    year: int = Query(..., description="Model year"),
) -> list[str]:
    """Get available makes for a specific model year."""
    return await CommunicationService.get_discovery_makes(year)


@router.get(
    "/discovery/models",
    response_model=list[str],
    summary="Get models for a make/year",
    response_description="List of available models",
)
async def get_models(
    year: int = Query(..., description="Model year"),
    make: str = Query(..., description="Make name"),
) -> list[str]:
    """Get available models for a specific year and make."""
    return await CommunicationService.get_discovery_models(year, make)


@router.get(
    "/discovery/variants",
    response_model=list[dict[str, Any]],
    summary="Get vehicle variants",
    response_description="List of vehicle variants with IDs",
)
async def get_variants(
    year: int = Query(..., description="Model year"),
    make: str = Query(..., description="Make name"),
    model: str = Query(..., description="Model name"),
) -> list[dict[str, Any]]:
    """Get specific vehicle variants and their IDs."""
    # Using list[dict] instead of VehicleVariant schema because CamelModel might conflict with direct API response
    return await CommunicationService.get_discovery_variants(year, make, model)


