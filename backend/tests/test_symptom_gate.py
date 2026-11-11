"""Symptom search needs an entitlement and is capped at 30 queries a day (JIP-1588).

`firebase.verify` and `service.search` are the only faked boundaries; the stub
records its ledger row the way the real search does. Runs against the real
database, deleting its own TESTSYM rows.
"""

from datetime import timedelta

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app
from src.symptom_search import router as router_mod
from src.symptom_search import service
from src.usage.ledger import record_usage

pytestmark = pytest.mark.asyncio

PREFIX = "TESTSYM-"
ADMIN = PREFIX + "admin"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTSYMMAKE"
YEAR = 2033
MODEL_TAG = "TESTSYMMODEL"
_app = create_app()

_CANNED = {
    "answer": "Your issue matches [1].",
    "refused": False,
    "confidence": 0.8,
    "model": "m",
    "answer_generated": True,
    "matches": [],
    "cited_ids": [],
    "cost": {
        "embed_tokens": 1,
        "embed_cost_usd": 0.0,
        "llm_model": "m",
        "llm_tokens_in": 1,
        "llm_tokens_out": 1,
        "llm_cost_usd": 0.0,
        "total_usd": 0.0,
    },
}


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


@pytest.fixture
def search_calls(monkeypatch):
    monkeypatch.setenv("ADMIN_FIREBASE_UIDS", ADMIN)
    get_settings.cache_clear()
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )
    monkeypatch.setattr(router_mod._limiter, "allow", lambda key: True)
    calls: list[dict] = []

    async def stub(vehicle_id, description, *, model=None, user_id=None):
        calls.append({"vehicle_id": vehicle_id, "user_id": user_id})
        await record_usage(
            "answer_compose",
            "claude-haiku-4-5",
            tokens_in=1,
            meta={"vehicle_id": vehicle_id, "user_id": user_id, "test": PREFIX},
        )
        return _CANNED

    monkeypatch.setattr(service, "search", stub)
    yield calls
    get_settings.cache_clear()


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM usage_events WHERE meta->>'test' = $1", PREFIX)
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


@pytest_asyncio.fixture
async def db(search_calls):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe(conn)
        yield conn
        await _wipe(conn)
    finally:
        await conn.close()


async def _user(conn, uid: str) -> int:
    return await conn.fetchval(
        "INSERT INTO users (firebase_uid, email) VALUES ($1, $2) RETURNING id",
        uid, f"{uid}@example.com",
    )


async def _vehicle(conn) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
        YEAR, MAKE, MODEL_TAG,
    )


async def _link(conn, user_id: int, vehicle_id: int) -> None:
    await conn.execute(
        "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2)", user_id, vehicle_id
    )


async def _unlock(conn, user_id: int, vehicle_id: int) -> None:
    await conn.execute(
        "INSERT INTO entitlements (user_id, kind, vehicle_id, status)"
        " VALUES ($1, 'unlock', $2, 'active')",
        user_id, vehicle_id,
    )


async def _seed_usage(conn, user_id: int, count: int, age: timedelta) -> None:
    await conn.execute(
        """
        INSERT INTO usage_events (kind, model, occurred_at, meta)
        SELECT 'answer_compose', 'claude-haiku-4-5', now() - $3::interval,
               jsonb_build_object('user_id', $1::bigint, 'test', $2::text)
        FROM generate_series(1, $4)
        """,
        user_id, PREFIX, age, count,
    )


async def _search(uid: str | None, vehicle_id: int) -> httpx.Response:
    headers = _as(uid) if uid else {}
    async with _http() as c:
        return await c.post(
            "/api/symptom-search",
            headers=headers,
            json={"vehicleId": vehicle_id, "description": "no heat"},
        )


async def test_anonymous_is_401(db, search_calls):
    vid = await _vehicle(db)
    r = await _search(None, vid)
    assert r.status_code == 401
    assert search_calls == []


async def test_linked_vehicle_without_entitlement_is_402(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    r = await _search(USER_A, vid)
    assert r.status_code == 402
    assert r.json() == {"detail": "unlock this vehicle or subscribe to search it"}
    assert search_calls == []


async def test_unlocked_vehicle_runs_and_the_ledger_row_carries_the_user_id(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    r = await _search(USER_A, vid)
    assert r.status_code == 200
    assert search_calls == [{"vehicle_id": vid, "user_id": uid}]
    n = await db.fetchval(
        "SELECT count(*) FROM usage_events WHERE meta->>'test' = $1 AND meta->>'user_id' = $2",
        PREFIX, str(uid),
    )
    assert n == 1


async def test_a_vehicle_linked_to_someone_else_is_404(db, search_calls):
    a = await _user(db, USER_A)
    await _user(db, USER_B)
    vid = await _vehicle(db)
    await _link(db, a, vid)
    await _unlock(db, a, vid)
    r = await _search(USER_B, vid)
    assert r.status_code == 404
    assert search_calls == []


async def test_thirty_queries_in_the_last_day_is_429(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _seed_usage(db, uid, 30, timedelta(hours=23))
    r = await _search(USER_A, vid)
    assert r.status_code == 429
    assert r.json() == {"detail": "daily search limit reached"}
    assert search_calls == []


async def test_twenty_nine_queries_still_allow_the_thirtieth(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _seed_usage(db, uid, 29, timedelta(hours=1))
    assert (await _search(USER_A, vid)).status_code == 200
    assert (await _search(USER_A, vid)).status_code == 429


async def test_thirty_queries_older_than_a_day_do_not_count(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _seed_usage(db, uid, 30, timedelta(hours=25))
    r = await _search(USER_A, vid)
    assert r.status_code == 200


async def test_another_users_queries_do_not_count_against_me(db, search_calls):
    a = await _user(db, USER_A)
    b = await _user(db, USER_B)
    vid = await _vehicle(db)
    await _link(db, a, vid)
    await _unlock(db, a, vid)
    await _seed_usage(db, b, 30, timedelta(hours=1))
    r = await _search(USER_A, vid)
    assert r.status_code == 200


async def test_admin_needs_no_link_no_entitlement_and_has_no_limit(db, search_calls):
    admin = await _user(db, ADMIN)
    vid = await _vehicle(db)
    await _seed_usage(db, admin, 30, timedelta(hours=1))
    r = await _search(ADMIN, vid)
    assert r.status_code == 200
    assert search_calls == [{"vehicle_id": vid, "user_id": admin}]


async def test_a_non_admin_with_an_admin_looking_uid_gets_no_bypass(db, search_calls):
    vid = await _vehicle(db)
    r = await _search(PREFIX + "admin-not", vid)
    assert r.status_code == 404


# --- 1,000 searches per payment (migration 0024) ----------------------------------------


async def _seed_searches(conn, user_id: int, count: int, age: timedelta) -> None:
    await conn.execute(
        """
        INSERT INTO usage_events (kind, model, occurred_at, meta)
        SELECT 'search_embed', 'gemini-embedding-001', now() - $3::interval,
               jsonb_build_object('user_id', $1::bigint, 'test', $2::text)
        FROM generate_series(1, $4)
        """,
        user_id, PREFIX, age, count,
    )


async def _paid(conn, user_id: int, age: timedelta) -> None:
    await conn.execute(
        "UPDATE users SET searches_reset_at = now() - $2::interval WHERE id = $1", user_id, age
    )


async def test_a_thousand_searches_since_the_last_payment_is_429(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _paid(db, uid, timedelta(days=90))
    await _seed_searches(db, uid, 1000, timedelta(days=30))
    r = await _search(USER_A, vid)
    assert r.status_code == 429
    assert "1,000 searches" in r.json()["detail"]
    assert search_calls == []


async def test_nine_hundred_ninety_nine_still_allow_one_more(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _paid(db, uid, timedelta(days=90))
    await _seed_searches(db, uid, 999, timedelta(days=30))
    assert (await _search(USER_A, vid)).status_code == 200


async def test_a_payment_starts_the_thousand_again_from_zero(db, search_calls):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _seed_searches(db, uid, 1000, timedelta(days=30))  # all used up...
    await _paid(db, uid, timedelta(days=1))  # ...then they paid again yesterday
    assert (await _search(USER_A, vid)).status_code == 200


async def test_an_account_that_never_had_a_reset_counts_every_search_it_ever_made(
    db, search_calls
):
    uid = await _user(db, USER_A)
    vid = await _vehicle(db)
    await _link(db, uid, vid)
    await _unlock(db, uid, vid)
    await _seed_searches(db, uid, 1000, timedelta(days=400))
    assert (await _search(USER_A, vid)).status_code == 429
