"""/api/me: who is signed in, their vehicles, and what they may read."""

from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.auth.deps import current_user
from src.corpus.router import _csv
from src.me import service
from src.me.schemas import (
    AlertsStatus,
    AlertsUpdate,
    MarkAllReadResponse,
    MeResponse,
    MyBulletinFull,
    MyBulletinLocked,
    MyBulletinsResponse,
    MyVehicle,
    MyVehicleCreate,
    MyVehicleUpdate,
)

router = APIRouter(prefix="/me", tags=["Me"])

# One body for "not yours" and "does not exist", so a vehicle id reveals nothing.
_NOT_FOUND = "vehicle not found"


@router.get("", response_model=MeResponse, summary="The signed-in user's profile")
async def get_me(user: dict = Depends(current_user)) -> MeResponse:
    """Id, email, admin flag, entitlements and linked vehicles."""
    return MeResponse(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
        is_admin=user["is_admin"],
        entitlements=await service.list_entitlements(user["id"]),
        vehicles=await service.list_my_vehicles(user["id"]),
        alerts=await service.get_alerts(user),
        removals=await service.get_removals(user["id"]),
    )


@router.post("/alerts", response_model=AlertsStatus, summary="Turn daily alerts on or off")
async def set_alerts(payload: AlertsUpdate, user: dict = Depends(current_user)) -> AlertsStatus:
    """Off is always allowed; on needs an active subscription (or admin), else 402."""
    try:
        return await service.set_alerts(user, payload.enabled)
    except service.NotEligibleForAlerts:
        raise HTTPException(
            status_code=402, detail="Subscribe to get daily alerts"
        ) from None


@router.post(
    "/vehicles",
    response_model=MyVehicle,
    status_code=status.HTTP_201_CREATED,
    summary="Add a vehicle to my account",
)
async def add_vehicle(payload: MyVehicleCreate, user: dict = Depends(current_user)) -> MyVehicle:
    """Link a vehicle to the caller, creating and ingesting it when it is new."""
    try:
        return await service.add_vehicle(user["id"], payload.year, payload.make, payload.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except service.SampleLimitReached:
        raise HTTPException(
            status_code=402,
            detail=(
                f"You've used your {service.FREE_SAMPLE_VEHICLES} free samples. "
                "Subscribe to add more cars, or unlock one you already have."
            ),
        ) from None
    except service.PlanLimitReached as e:
        more = (
            " Remove one, unlock a car for $3, or move to All access for "
            f"{service.PLAN_VEHICLES['all_access']} cars."
            if e.plan == "monthly"
            else " Remove one to add another."
        )
        raise HTTPException(
            status_code=402, detail=f"Your plan covers {e.limit} cars.{more}"
        ) from None
    except service.VehicleLimitReached:
        raise HTTPException(
            status_code=409, detail=f"at most {service.MAX_VEHICLES} vehicles per account"
        ) from None


@router.delete(
    "/vehicles/{vehicle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a vehicle from my account",
)
async def remove_vehicle(vehicle_id: int, user: dict = Depends(current_user)) -> Response:
    """Unlink the caller's vehicle; 404 when not linked, 409 past the plan's monthly removals."""
    try:
        removed = await service.remove_vehicle(user["id"], vehicle_id)
    except service.RemovalLimitReached as e:
        cars = "1 car" if e.limit == 1 else f"{e.limit} cars"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Your plan lets you remove {cars} a month. "
                f"You can remove another from {e.resets_on:%B} {e.resets_on.day}."
            ),
        ) from None
    if not removed:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/vehicles/{vehicle_id}",
    response_model=MyVehicle,
    summary="Include or leave out one of my vehicles in alert emails",
)
async def update_vehicle(
    vehicle_id: int, payload: MyVehicleUpdate, user: dict = Depends(current_user)
) -> MyVehicle:
    """Per-car alert switch; 404 when the caller has no such link."""
    vehicle = await service.set_vehicle_alerts(user["id"], vehicle_id, payload.alerts_enabled)
    if vehicle is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return MyVehicle(**vehicle)


@router.get(
    "/vehicles/{vehicle_id}/bulletins",
    response_model=MyBulletinsResponse,
    response_model_exclude_unset=True,
    summary="Bulletins for one of my vehicles, locked beyond the free sample",
)
async def my_bulletins(
    vehicle_id: int,
    severity: Literal["serious", "medium", "small"] | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200, alias="perPage"),
    search: str | None = Query(None, max_length=200),
    unread_only: bool = Query(False, alias="unreadOnly"),
    comm_type: str | None = Query(None, alias="commType", max_length=20),
    systems: str | None = Query(None, max_length=500),
    date_from: date | None = Query(None, alias="dateFrom"),
    date_to: date | None = Query(None, alias="dateTo"),
    nhtsa_id: str | None = Query(None, alias="nhtsaId", max_length=100),
    user: dict = Depends(current_user),
):
    """Folded bulletins newest first; 404 unless the vehicle is linked to the caller."""
    result = await service.my_bulletins(
        user["id"],
        vehicle_id,
        severity,
        page,
        per_page,
        search,
        unread_only=unread_only,
        comm_type=comm_type,
        systems=_csv(systems),
        date_from=datetime.combine(date_from, time.min, timezone.utc) if date_from else None,
        date_to=datetime.combine(date_to, time.max, timezone.utc) if date_to else None,
        nhtsa_id=nhtsa_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return MyBulletinsResponse(**result)


@router.get(
    "/vehicles/{vehicle_id}/bulletins/{nhtsa_id}",
    response_model=MyBulletinFull | MyBulletinLocked,
    summary="One bulletin of my vehicle: full detail, or identity only when locked",
)
async def my_bulletin_detail(
    vehicle_id: int, nhtsa_id: str, user: dict = Depends(current_user)
) -> MyBulletinFull | MyBulletinLocked:
    """404 unless the vehicle is linked to the caller and the id is one of its bulletins."""
    result = await service.my_bulletin_detail(user["id"], vehicle_id, nhtsa_id)
    if result is None:
        raise HTTPException(status_code=404, detail="bulletin not found")
    return result


@router.post(
    "/bulletins/{nhtsa_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark one bulletin read (idempotent)",
)
async def mark_read(nhtsa_id: str, user: dict = Depends(current_user)) -> Response:
    """404 unless the bulletin is on one of the caller's linked vehicles."""
    if not await service.mark_read(user["id"], nhtsa_id):
        raise HTTPException(status_code=404, detail="bulletin not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/vehicles/{vehicle_id}/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark every unread bulletin of one of my vehicles read",
)
async def mark_all_read(
    vehicle_id: int,
    severity: Literal["serious", "medium", "small"] | None = None,
    user: dict = Depends(current_user),
) -> MarkAllReadResponse:
    """Optionally only one severity; 404 unless the vehicle is linked to the caller."""
    marked = await service.mark_all_read(user["id"], vehicle_id, severity)
    if marked is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return MarkAllReadResponse(marked=marked)
