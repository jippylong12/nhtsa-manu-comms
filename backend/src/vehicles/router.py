"""API router for Vehicles feature."""

from fastapi import APIRouter, HTTPException, status

from src.vehicles.schemas import (
    VehicleCreate,
    VehicleUpdate,
    VehicleResponse,
    VehicleListResponse,
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
    vehicle = await VehicleService.create_vehicle(
        vehicle_id=payload.vehicle_id,
        year=payload.year,
        model=payload.model,
        keywords=payload.keywords,
    )
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
    summary="Get a vehicle by NHTSA ID",
    response_description="The requested vehicle",
)
async def get_vehicle(vehicle_id: int) -> VehicleResponse:
    """Get a single vehicle by its NHTSA Vehicle ID."""
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
    vehicle = await VehicleService.update_vehicle(
        vehicle_id=vehicle_id,
        year=payload.year,
        model=payload.model,
        keywords=payload.keywords,
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return VehicleResponse(**vehicle)


@router.delete(
    "/{vehicle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tracked vehicle",
    response_description="Vehicle deleted successfully",
)
async def delete_vehicle(vehicle_id: int) -> None:
    """Delete a vehicle and all its associated communications."""
    deleted = await VehicleService.delete_vehicle(vehicle_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Vehicle not found")
