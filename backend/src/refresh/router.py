"""API router for the in-app refresh (JIP-418).

Three endpoints, in the order the UI uses them:

    GET  /api/refresh/{vehicle_id}/preview   free; what a run would do and cost
    POST /api/refresh/{vehicle_id}           billed; starts the run
    GET  /api/refresh/{vehicle_id}           progress, polled while it runs

The run is a background task, not a held-open stream. It takes minutes, and a
user who navigates away and returns has to see it still going rather than a
stale or empty page.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.auth.deps import admin_user

from src.refresh import service
from src.refresh.schemas import RefreshPreview, RefreshRun

router = APIRouter(prefix="/refresh", tags=["Refresh"], dependencies=[Depends(admin_user)])


@router.get(
    "/{vehicle_id}/preview",
    response_model=RefreshPreview,
    summary="What a refresh would do, and what it would cost",
    response_description="An estimate. Nothing is spent producing it.",
)
async def preview_refresh(vehicle_id: int) -> RefreshPreview:
    """Ask NHTSA what exists and compare it against the corpus.

    Free by design, and it has to stay that way: the whole confirm-before-you-
    spend flow rests on the estimate being available before any money moves.
    """
    try:
        plan = await service.preview(vehicle_id)
    except httpx.HTTPError as e:
        # NHTSA being slow or down is not this app being broken, and the user
        # can act on it by trying again. The client already retried.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NHTSA did not respond. Try again in a moment.",
        ) from e
    if plan is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return RefreshPreview(**plan)


@router.post(
    "/{vehicle_id}",
    response_model=RefreshRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the pipeline for one vehicle",
    response_description="The run, which continues in the background",
)
async def start_refresh(vehicle_id: int) -> RefreshRun:
    """Sync from NHTSA, then read, analyse and index, for this vehicle only.

    Returns immediately with a run to poll. Call the preview first and show the
    user what they are agreeing to: this endpoint spends money.
    """
    try:
        run = await service.start(vehicle_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Vehicle not found") from e
    except service.RefreshConflict as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A refresh is already running.",
                "activeVehicleId": e.active.get("vehicle_id"),
            },
        ) from e
    return RefreshRun(**run)


@router.get(
    "/{vehicle_id}",
    response_model=RefreshRun,
    responses={204: {"description": "This vehicle has never been refreshed"}},
    summary="Progress of the current or most recent refresh",
    response_description="The run",
)
async def get_refresh(vehicle_id: int):
    """Poll this while a run is in flight.

    204 rather than 404 when there is no run: "this vehicle has never been
    refreshed" is an ordinary state for a newly added vehicle, not an error the
    client should have to surface. Returning the Response directly is what skips
    response_model validation on an empty body.
    """
    run = await service.latest_run(vehicle_id)
    if run is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return RefreshRun(**run)
