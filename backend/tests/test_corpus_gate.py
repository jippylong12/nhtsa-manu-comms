"""The legacy corpus read endpoints are admin-only (they bypass the paid sample rule).

`GET /api/corpus/communications` and `GET /api/corpus/communications/{nhtsa_id}`
return full summaries, symptoms, remedies and documents for any vehicle, so they
sit behind `admin_user`. Only `firebase.verify` is faked; the row is real and
TESTCGATE-named.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTCGATE-"
ADMIN = PREFIX + "admin"
USER = PREFIX + "user"
NHTSA_ID = PREFIX + "1"
_app = create_app()

PATHS = ["/api/corpus/communications", f"/api/corpus/communications/{NHTSA_ID}"]


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


@pytest_asyncio.fixture(autouse=True)
async def seeded(monkeypatch):
    monkeypatch.setenv("ADMIN_FIREBASE_UIDS", ADMIN)
    get_settings.cache_clear()
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", PREFIX + "%")
        await conn.execute(
            "INSERT INTO communications (nhtsa_id, communication_type, summary, status, severity) "
            "VALUES ($1, 'TSB', 'CGATE SUMMARY', 'processed', 'small')",
            NHTSA_ID,
        )
        yield
        await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", PREFIX + "%")
    finally:
        await conn.close()
        get_settings.cache_clear()


@pytest.mark.parametrize("path", PATHS)
async def test_anonymous_is_401(path):
    async with _http() as c:
        r = await c.get(path)
    assert r.status_code == 401


@pytest.mark.parametrize("path", PATHS)
async def test_signed_in_non_admin_is_403(path):
    async with _http() as c:
        r = await c.get(path, headers=_as(USER))
    assert r.status_code == 403
    assert r.json() == {"detail": "admin only"}


async def test_admin_still_reads_the_detail():
    async with _http() as c:
        r = await c.get(PATHS[1], headers=_as(ADMIN))
    assert r.status_code == 200
    body = r.json()
    assert body["nhtsaId"] == NHTSA_ID
    assert body["summary"] == "CGATE SUMMARY"
    assert body["documents"] == []


async def test_admin_still_reads_the_list():
    async with _http() as c:
        r = await c.get(PATHS[0], headers=_as(ADMIN), params={"search": "CGATE"})
    assert r.status_code == 200
    assert "items" in r.json()


async def test_tags_stay_open():
    async with _http() as c:
        r = await c.get("/api/corpus/tags")
    assert r.status_code == 200
