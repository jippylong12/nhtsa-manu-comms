"""API router for Communications feature.

Only the discovery cascade remains: the year, make, model, variant and trim
lookups that fill the add-vehicle form. The list, detail, stats and SSE fetch
endpoints were the Mongo-backed second path and are gone (JIP-417). Reading
communications is `/api/corpus`; fetching them is `/api/refresh`.
"""

from typing import Any, Optional

from fastapi import APIRouter, Query

from src.communications.service import CommunicationService

router = APIRouter(prefix="/communications", tags=["Communications"])


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
    summary="Get vehicle variants with vehicleId",
    response_description="List of vehicle variants with correct vehicleId for communications",
)
async def get_variants(
    year: int = Query(..., description="Model year"),
    make: str = Query(..., description="Make name"),
    model: str = Query(..., description="Model name"),
    trim: Optional[str] = Query(None, description="Body style/trim (e.g., PU/CC, SUV)"),
) -> list[dict[str, Any]]:
    """Get specific vehicle variants with their correct vehicleId for communications API."""
    return await CommunicationService.get_discovery_variants(year, make, model, trim)


@router.get(
    "/discovery/trims",
    response_model=list[str],
    summary="Get available trims/body styles for a model",
    response_description="List of available trims",
)
async def get_trims(
    year: int = Query(..., description="Model year"),
    make: str = Query(..., description="Make name"),
    model: str = Query(..., description="Model name"),
) -> list[str]:
    """Get available trims/body styles for a specific year/make/model."""
    return await CommunicationService.get_discovery_trims(year, make, model)

