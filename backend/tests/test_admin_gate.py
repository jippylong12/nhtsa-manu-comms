"""Every write endpoint needs an admin; GET /api/me says who is signed in (JIP-1582).

`src.auth.firebase.verify` is the only faked boundary. Runs against the real
database, deleting its own TESTGATE rows.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.communications import flatfile
from src.config import get_settings
from src.main import create_app

PREFIX = "TESTGATE-"
ADMIN_UID = PREFIX + "admin"
USER_UID = PREFIX + "user"
YEAR, MAKE, MODEL = 2019, "MAZDA", "CX 5"

_app = create_app()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setenv("ADMIN_FIREBASE_UIDS", ADMIN_UID)
    get_settings.cache_clear()
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "Test Person"},
    )

    async def no_scan(*a, **kw):
        return {}

    monkeypatch.setattr(flatfile, "refresh_index", no_scan)
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def clean():
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        c = await asyncpg.connect(settings.database_url)
        try:
            await c.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
            await c.execute(
                "DELETE FROM vehicles WHERE year = $1 AND make = $2 AND model = $3",
                YEAR, MAKE, MODEL,
            )
        finally:
            await c.close()

    await wipe()
    yield
    await wipe()


VEHICLE = {"year": str(YEAR), "make": MAKE, "model": MODEL}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [("POST", "/api/vehicles"), ("POST", "/api/refresh/1"), ("GET", "/api/admin/usage"),
     ("PATCH", "/api/vehicles/1"), ("DELETE", "/api/vehicles/1"), ("GET", "/api/refresh/1")],
)
async def test_anonymous_write_is_401(method, path):
    async with _http() as c:
        r = await c.request(method, path, json=VEHICLE if method in ("POST", "PATCH") else None)
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [("POST", "/api/vehicles"), ("POST", "/api/refresh/1"), ("GET", "/api/admin/usage")],
)
async def test_plain_user_gets_403_admin_only(method, path):
    async with _http() as c:
        r = await c.request(
            method, path, headers=_as(USER_UID), json=VEHICLE if method == "POST" else None
        )
    assert r.status_code == 403
    assert r.json() == {"detail": "admin only"}


@pytest.mark.asyncio
async def test_admin_creates_a_vehicle_as_before():
    async with _http() as c:
        r = await c.post("/api/vehicles", headers=_as(ADMIN_UID), json=VEHICLE)
    assert r.status_code == 201
    assert r.json()["make"] == MAKE


@pytest.mark.asyncio
async def test_admin_reads_usage():
    async with _http() as c:
        r = await c.get("/api/admin/usage", headers=_as(ADMIN_UID))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_me_returns_profile_for_a_plain_user():
    async with _http() as c:
        r = await c.get("/api/me", headers=_as(USER_UID))
    body = r.json()
    assert r.status_code == 200
    assert body["email"] == f"{USER_UID}@example.com"
    assert body["displayName"] == "Test Person"
    assert body["isAdmin"] is False
    assert body["entitlements"] == []
    assert body["vehicles"] == []
    assert isinstance(body["id"], int)


@pytest.mark.asyncio
async def test_me_flags_the_admin():
    async with _http() as c:
        r = await c.get("/api/me", headers=_as(ADMIN_UID))
    assert r.json()["isAdmin"] is True


@pytest.mark.asyncio
async def test_me_lists_entitlements():
    async with _http() as c:
        me = (await c.get("/api/me", headers=_as(USER_UID))).json()
        conn = await asyncpg.connect(get_settings().database_url)
        try:
            await conn.execute(
                "INSERT INTO entitlements (user_id, kind, status) VALUES ($1, 'subscription', 'active')",
                me["id"],
            )
        finally:
            await conn.close()
        r = await c.get("/api/me", headers=_as(USER_UID))
    assert r.json()["entitlements"] == [
        {"kind": "subscription", "vehicleId": None, "status": "active", "currentPeriodEnd": None}
    ]


@pytest.mark.asyncio
async def test_me_without_sign_in_is_401():
    async with _http() as c:
        r = await c.get("/api/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_discovery_stays_anonymous():
    async with _http() as c:
        r = await c.get("/api/communications/discovery/years")
    assert r.status_code == 200
