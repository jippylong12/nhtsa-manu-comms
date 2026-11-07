"""FastAPI dependencies that say who is calling."""

from fastapi import Depends, Header, HTTPException

from src.auth import firebase, repository
from src.config import get_settings
from src.db import get_pool


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="sign in required")


async def _user_from_header(authorization: str) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    try:
        claims = firebase.verify(token.strip())
    except firebase.AuthError:
        raise _unauthorized() from None
    uid = claims["uid"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await repository.upsert_user(
            conn,
            uid,
            claims.get("email") or "",
            claims.get("name"),
            uid in get_settings().admin_firebase_uids,
        )


async def current_user(authorization: str | None = Header(None)) -> dict:
    """The signed-in user's row (created on first sight), or 401."""
    if not authorization:
        raise _unauthorized()
    return await _user_from_header(authorization)


async def optional_user(authorization: str | None = Header(None)) -> dict | None:
    """The signed-in user, or None when no Authorization header is sent."""
    if not authorization:
        return None
    return await _user_from_header(authorization)


async def admin_user(user: dict = Depends(current_user)) -> dict:
    """The signed-in user, who must be an admin, or 403."""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="admin only")
    return user
