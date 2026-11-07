"""GET /api/me: who is signed in, and what they have bought."""

from fastapi import APIRouter, Depends

from src.auth.deps import current_user
from src.me import service
from src.me.schemas import MeResponse

router = APIRouter(prefix="/me", tags=["Me"])


@router.get("", response_model=MeResponse, summary="The signed-in user's profile")
async def get_me(user: dict = Depends(current_user)) -> MeResponse:
    """Id, email, admin flag and entitlements. Vehicles are filled in M3."""
    return MeResponse(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
        is_admin=user["is_admin"],
        entitlements=await service.list_entitlements(user["id"]),
        vehicles=[],
    )
