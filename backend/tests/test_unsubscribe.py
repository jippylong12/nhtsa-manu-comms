"""Unsubscribe link and the account alerts toggle (JIP-1594).

`firebase.verify` is the only faked boundary. Runs against the real database on
committed connections and deletes exactly its own TESTUNS rows.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTUNS-"
SUB = PREFIX + "sub"
FREE = PREFIX + "free"
ADMIN = PREFIX + "admin"
_app = create_app()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")


async def _user(conn, uid: str, *, admin: bool = False, subscribed: bool = False) -> int:
    user_id = await conn.fetchval(
        "INSERT INTO users (firebase_uid, email, is_admin) VALUES ($1, $2, $3) RETURNING id",
        uid, f"{uid}@example.com", admin,
    )
    if subscribed:
        await conn.execute(
            "INSERT INTO entitlements (user_id, kind, status) VALUES ($1, 'subscription', 'active')",
            user_id,
        )
    return user_id


@pytest_asyncio.fixture
async def db(monkeypatch):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe(conn)
        yield conn
        await _wipe(conn)
    finally:
        await conn.close()


async def _state(conn, user_id: int):
    return await conn.fetchrow("SELECT * FROM alert_state WHERE user_id = $1", user_id)


async def _token(conn, user_id: int) -> str:
    await conn.execute("INSERT INTO alert_state (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    return (await _state(conn, user_id))["unsubscribe_token"]


async def test_a_valid_link_unsubscribes_and_a_second_visit_says_already(db):
    uid = await _user(db, SUB, subscribed=True)
    token = await _token(db, uid)
    async with _http() as c:
        first = await c.get(f"/api/public/unsubscribe/{token}")
        second = await c.get(f"/api/public/unsubscribe/{token}")
    assert first.status_code == 200
    assert first.json() == {"status": "unsubscribed"}
    assert first.headers["cache-control"] == "no-store"
    assert (await _state(db, uid))["unsubscribed_at"] is not None
    stamp = (await _state(db, uid))["unsubscribed_at"]
    assert second.json() == {"status": "already"}
    assert (await _state(db, uid))["unsubscribed_at"] == stamp


async def test_unknown_and_malformed_tokens_are_the_same_404(db):
    async with _http() as c:
        unknown = await c.get("/api/public/unsubscribe/" + "0" * 48)
        malformed = await c.get("/api/public/unsubscribe/not%20a%20token")
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json() == malformed.json()
    assert unknown.headers["cache-control"] == "no-store"


async def test_the_link_still_works_in_read_only_mode(db, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "true")
    get_settings.cache_clear()
    try:
        app = create_app()
        uid = await _user(db, SUB, subscribed=True)
        token = await _token(db, uid)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(f"/api/public/unsubscribe/{token}")
        assert r.json() == {"status": "unsubscribed"}
    finally:
        monkeypatch.delenv("READ_ONLY")
        get_settings.cache_clear()


async def test_me_shows_alerts_enabled_for_a_user_with_no_state_row_yet(db):
    uid = await _user(db, SUB, subscribed=True)
    async with _http() as c:
        me = (await c.get("/api/me", headers=_as(SUB))).json()
    assert me["alerts"] == {"enabled": True, "lastSentAt": None, "eligible": True}
    assert (await _state(db, uid)) is not None


async def test_turning_alerts_off_then_on_clears_the_unsubscribe(db):
    uid = await _user(db, SUB, subscribed=True)
    async with _http() as c:
        off = await c.post("/api/me/alerts", headers=_as(SUB), json={"enabled": False})
        off_me = (await c.get("/api/me", headers=_as(SUB))).json()
        on = await c.post("/api/me/alerts", headers=_as(SUB), json={"enabled": True})
        on_me = (await c.get("/api/me", headers=_as(SUB))).json()
    assert off.status_code == on.status_code == 200
    assert off.json()["enabled"] is False
    assert off_me["alerts"]["enabled"] is False
    assert on_me["alerts"]["enabled"] is True
    assert (await _state(db, uid))["unsubscribed_at"] is None


async def test_an_unsubscribed_link_visitor_can_turn_alerts_back_on_from_the_account(db):
    uid = await _user(db, SUB, subscribed=True)
    token = await _token(db, uid)
    async with _http() as c:
        await c.get(f"/api/public/unsubscribe/{token}")
        me = (await c.get("/api/me", headers=_as(SUB))).json()
        await c.post("/api/me/alerts", headers=_as(SUB), json={"enabled": True})
        again = await c.get(f"/api/public/unsubscribe/{token}")
    assert me["alerts"]["enabled"] is False
    assert again.json() == {"status": "unsubscribed"}


async def test_turning_on_without_a_subscription_is_402_but_turning_off_is_allowed(db):
    uid = await _user(db, FREE)
    async with _http() as c:
        me = (await c.get("/api/me", headers=_as(FREE))).json()
        on = await c.post("/api/me/alerts", headers=_as(FREE), json={"enabled": True})
        off = await c.post("/api/me/alerts", headers=_as(FREE), json={"enabled": False})
    assert me["alerts"]["eligible"] is False
    assert on.status_code == 402
    assert on.json()["detail"]
    assert off.status_code == 200
    assert (await _state(db, uid))["unsubscribed_at"] is not None


async def test_an_admin_is_eligible_without_a_subscription(db):
    await _user(db, ADMIN, admin=True)
    async with _http() as c:
        me = (await c.get("/api/me", headers=_as(ADMIN))).json()
        await c.post("/api/me/alerts", headers=_as(ADMIN), json={"enabled": False})
        on = await c.post("/api/me/alerts", headers=_as(ADMIN), json={"enabled": True})
    assert me["alerts"]["eligible"] is True
    assert on.status_code == 200


async def test_alerts_need_a_signed_in_user(db):
    async with _http() as c:
        r = await c.post("/api/me/alerts", json={"enabled": True})
    assert r.status_code == 401
