"""Firebase identity: a verified ID token becomes a `users` row (JIP-1581).

`src.auth.firebase.verify` is the only faked boundary; no Firebase call is made.
Runs against the real database, deleting its own TESTAUTH rows.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI

from src.auth import firebase
from src.auth.deps import admin_user, current_user
from src.config import get_settings

PREFIX = "TESTAUTH-"

_app = FastAPI()


@_app.get("/me")
async def _me(user: dict = Depends(current_user)):
    return {"id": user["id"], "uid": user["firebase_uid"], "email": user["email"],
            "isAdmin": user["is_admin"]}


@_app.get("/admin")
async def _admin(user: dict = Depends(admin_user)):
    return {"uid": user["firebase_uid"]}


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _signed_in_as(monkeypatch, uid: str, email: str = "a@b.c", name: str = "A") -> None:
    monkeypatch.setattr(
        firebase, "verify", lambda token: {"uid": uid, "email": email, "name": name}
    )


BEARER = {"Authorization": "Bearer good"}


@pytest_asyncio.fixture(autouse=True)
async def clean():
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        c = await asyncpg.connect(settings.database_url)
        try:
            await c.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
        finally:
            await c.close()

    await wipe()
    yield
    await wipe()


async def _rows(uid: str) -> list[asyncpg.Record]:
    c = await asyncpg.connect(get_settings().database_url)
    try:
        return await c.fetch("SELECT * FROM users WHERE firebase_uid = $1", uid)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_no_header_is_401_sign_in_required():
    async with _http() as c:
        r = await c.get("/me")
    assert r.status_code == 401
    assert r.json() == {"detail": "sign in required"}


@pytest.mark.asyncio
async def test_non_bearer_header_is_401():
    async with _http() as c:
        r = await c.get("/me", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json() == {"detail": "sign in required"}


@pytest.mark.asyncio
async def test_invalid_token_is_401(monkeypatch):
    def reject(token):
        raise firebase.AuthError("bad")

    monkeypatch.setattr(firebase, "verify", reject)
    async with _http() as c:
        r = await c.get("/me", headers=BEARER)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_first_call_creates_the_user_and_the_second_does_not_duplicate(monkeypatch):
    uid = PREFIX + "new"
    _signed_in_as(monkeypatch, uid, "new@example.com")
    async with _http() as c:
        first = await c.get("/me", headers=BEARER)
        before = (await _rows(uid))[0]["last_seen_at"]
        second = await c.get("/me", headers=BEARER)
    assert first.status_code == 200
    assert first.json()["uid"] == uid and first.json()["email"] == "new@example.com"
    assert first.json()["isAdmin"] is False
    assert second.json()["id"] == first.json()["id"]
    rows = await _rows(uid)
    assert len(rows) == 1
    assert rows[0]["last_seen_at"] > before


@pytest.mark.asyncio
async def test_admin_uid_from_settings_is_admin_and_passes_admin_user(monkeypatch):
    uid = PREFIX + "boss"
    _signed_in_as(monkeypatch, uid)
    monkeypatch.setattr(get_settings(), "admin_firebase_uids", [uid])
    async with _http() as c:
        me = await c.get("/me", headers=BEARER)
        adm = await c.get("/admin", headers=BEARER)
    assert me.json()["isAdmin"] is True
    assert adm.status_code == 200


@pytest.mark.asyncio
async def test_plain_user_gets_403_on_admin_route(monkeypatch):
    _signed_in_as(monkeypatch, PREFIX + "plain")
    async with _http() as c:
        r = await c.get("/admin", headers=BEARER)
    assert r.status_code == 403
    assert r.json() == {"detail": "admin only"}


def test_admin_uids_accept_a_comma_separated_string():
    from src.config import Settings

    assert Settings(admin_firebase_uids="a, b,c").admin_firebase_uids == ["a", "b", "c"]
