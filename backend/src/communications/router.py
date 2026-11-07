"""API router for Communications feature.

Only the discovery cascade remains: the year, make and model lookups that fill the
add-vehicle form, now served from the ``comm_vocab`` flat-file vocabulary (ADR 0001,
M3). The variant and trim endpoints were the byYmmt resolve step and are gone --
association happens on the plain nameplate at fetch time, so the picker never
resolves a ``vehicleId`` and can no longer be blocked by NHTSA. The list, detail,
stats and SSE fetch endpoints were the Mongo-backed second path and went earlier
(JIP-417). Reading communications is ``/api/corpus``; fetching them is
``/api/refresh``.
"""

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
    response_description="List of available plain-nameplate models",
)
async def get_models(
    year: int = Query(..., description="Model year"),
    make: str = Query(..., description="Make name"),
) -> list[str]:
    """Get available plain nameplates for a specific year and make."""
    return await CommunicationService.get_discovery_models(year, make)
