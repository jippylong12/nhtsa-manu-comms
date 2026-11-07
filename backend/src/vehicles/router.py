"""API router for Vehicles feature."""

from fastapi import APIRouter, HTTPException, status

from src.vehicles.schemas import (
    VehicleCreate,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdate,
)
from src.vehicles.service import VehicleService

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


@router.post(
    "",
    response_model=VehicleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a vehicle to track",
    response_description="The created or existing vehicle",
)
async def create_vehicle(payload: VehicleCreate) -> VehicleResponse:
    """Add a new vehicle to track for manufacturer communications."""
    try:
        vehicle = await VehicleService.create_vehicle(
            year=payload.year,
            make=payload.make,
            model=payload.model,
            keywords=payload.keywords,
        )
    except ValueError as e:
        # The year column is an integer; the API takes it as a string.
        raise HTTPException(status_code=400, detail=str(e)) from e
    return VehicleResponse(**vehicle)


@router.get(
    "",
    response_model=VehicleListResponse,
    summary="List all tracked vehicles",
    response_description="Paginated list of vehicles",
)
async def list_vehicles(page: int = 1, per_page: int = 20) -> VehicleListResponse:
    """Get a paginated list of all tracked vehicles."""
    vehicles, total = await VehicleService.list_vehicles(page=page, per_page=per_page)
    return VehicleListResponse(
        items=[VehicleResponse(**v) for v in vehicles],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Get a vehicle by its id",
    response_description="The requested vehicle",
)
async def get_vehicle(vehicle_id: int) -> VehicleResponse:
    """Get a single vehicle by its id."""
    vehicle = await VehicleService.get_vehicle(vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return VehicleResponse(**vehicle)


@router.patch(
    "/{vehicle_id}",
    response_model=VehicleResponse,
    summary="Update vehicle configuration",
    response_description="The updated vehicle",
)
async def update_vehicle(vehicle_id: int, payload: VehicleUpdate) -> VehicleResponse:
    """Update a vehicle's tracking configuration."""
    try:
        vehicle = await VehicleService.update_vehicle(
            vehicle_id=vehicle_id,
            keywords=payload.keywords,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return VehicleResponse(**vehicle)


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop tracking a vehicle",
    response_description="Vehicle removed successfully",
)
async def delete_vehicle(vehicle_id: int) -> None:
    """Stop tracking a vehicle.

    Deactivates rather than deletes, so bulletins shared with other vehicles
    keep their links. The vehicle disappears from the API either way.
    """
    deleted = await VehicleService.delete_vehicle(vehicle_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Vehicle not found")
