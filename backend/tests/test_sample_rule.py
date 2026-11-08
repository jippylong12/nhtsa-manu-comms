"""The free sample rule and entitlements on GET /api/me/vehicles/{id}/bulletins (JIP-1587).

Newest 1 serious, 2 medium, 3 small stay readable; everything else is locked down to
its identity fields. Only `firebase.verify` is faked; rows are real and TESTSAMP-named.
"""

import json

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.billing.entitlements import access_for
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTSAMP-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
ADMIN = PREFIX + "admin"
MAKE = "TESTSAMPMAKE"
SECRET = "SECRETSUMMARYTEXT"
LOCKED_KEYS = {
    "nhtsaId", "communicationNumber", "communicationType", "communicationDate",
    "severity", "supersededCount", "locked",
}
_app = create_app()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


@pytest_asyncio.fixture
async def db(monkeypatch):
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
        await _wipe(conn)
        yield conn
        await _wipe(conn)
    finally:
        await conn.close()
        get_settings.cache_clear()


async def _vehicle(conn, model: str) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2033, $1, $2) RETURNING id", MAKE, model
    )


async def _comm(conn, vid, tag, severity, date, number=None, document=True):
    cid = await conn.fetchval(
        """
        INSERT INTO communications (nhtsa_id, communication_number, communication_type,
            communication_date, summary, status, severity)
        VALUES ($1, $2, 'TSB', $3::text::date, $4, 'processed', $5) RETURNING id
        """,
        PREFIX + tag, number or "TS" + tag.replace("-", "") + "00", date, SECRET, severity,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid, vid,
    )
    if document:
        await conn.execute(
            """
            INSERT INTO comm_documents (communication_id, url, llm_summary, symptoms, systems,
                components, remedy, applicability, applicability_conditions, audience)
            VALUES ($1, $2, $3, ARRAY['SECRETSYMPTOM'], ARRAY['SECRETSYSTEM'],
                ARRAY['SECRETCOMPONENT'], 'SECRETREMEDY', 'SECRETAPPLIC', 'SECRETCOND', 'owner')
            """,
            cid, f"https://example.test/{PREFIX}{tag}.pdf", SECRET,
        )
    return cid


async def _seed(conn, model="M1") -> int:
    tag = "" if model == "M1" else model + "-"
    """4 serious, 5 medium, 7 small; tag number = rank (higher = newer)."""
    vid = await _vehicle(conn, model)
    for sev, n in (("serious", 4), ("medium", 5), ("small", 7)):
        for i in range(n):
            await _comm(conn, vid, f"{tag}{sev}-{i}", sev, f"2090-01-{i + 1:02d}")
    return vid


async def _link(conn, uid: str, vid: int) -> int:
    user_id = await conn.fetchval(
        """INSERT INTO users (firebase_uid, email) VALUES ($1, $2)
           ON CONFLICT (firebase_uid) DO UPDATE SET email = EXCLUDED.email RETURNING id""",
        uid, uid + "@example.com",
    )
    await conn.execute(
        "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, vid,
    )
    return user_id


async def _entitle(conn, user_id, kind, status="active", vehicle_id=None):
    await conn.execute(
        "INSERT INTO entitlements (user_id, kind, vehicle_id, status) VALUES ($1, $2, $3, $4)",
        user_id, kind, vehicle_id, status,
    )


async def _get(uid, vid, **params):
    async with _http() as c:
        r = await c.get(f"/api/me/vehicles/{vid}/bulletins", headers=_as(uid), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _unlocked(body) -> set[str]:
    return {i["nhtsaId"].removeprefix(PREFIX) for i in body["items"] if not i["locked"]}


async def test_no_entitlement_unlocks_the_newest_1_serious_2_medium_3_small(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, perPage=200)
    assert body["access"] == "sample"
    assert body["total"] == 16
    assert body["sampleCounts"] == {"serious": 1, "medium": 2, "small": 3}
    assert _unlocked(body) == {
        "serious-3", "medium-4", "medium-3", "small-6", "small-5", "small-4",
    }


async def test_locked_rows_carry_only_identity_and_no_text(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, perPage=200)
    locked = [i for i in body["items"] if i["locked"]]
    assert len(locked) == 10
    for row in locked:
        assert set(row) == LOCKED_KEYS
        for text in ("SECRET", "summary", "remedy", "symptoms", "documents", "applicab"):
            assert text.lower() not in json.dumps(row).lower()
    full = next(i for i in body["items"] if not i["locked"])
    assert full["summary"] == SECRET
    assert full["remedy"] == "SECRETREMEDY"
    assert full["documents"] == [{"url": f"https://example.test/{full['nhtsaId']}.pdf"}]


async def test_rows_are_newest_first_and_ties_resolve_by_nhtsa_id_desc(db):
    vid = await _vehicle(db, "TIES")
    for tag in ("serious-a", "serious-c", "serious-b"):
        await _comm(db, vid, tag, "serious", "2090-05-05")
    await _comm(db, vid, "serious-z", "serious", "2090-01-01")
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid)
    assert [i["nhtsaId"].removeprefix(PREFIX) for i in body["items"]] == [
        "serious-c", "serious-b", "serious-a", "serious-z",
    ]
    assert _unlocked(body) == {"serious-c"}


async def test_the_sample_is_chosen_across_the_whole_list_not_the_page(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    second = await _get(USER_A, vid, perPage=5, page=2)
    assert second["total"] == 16
    assert len(second["items"]) == 5
    # page 2 holds ranks 6-10; the sample is the newest overall, never recomputed per page
    page1 = await _get(USER_A, vid, perPage=5, page=1)
    assert _unlocked(page1) | _unlocked(second) <= {
        "serious-3", "medium-4", "medium-3", "small-6", "small-5", "small-4",
    }


async def test_a_severity_filter_still_shows_the_sample_rows_full(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, severity="medium", perPage=200)
    assert body["total"] == 5
    assert _unlocked(body) == {"medium-4", "medium-3"}
    assert sum(1 for i in body["items"] if i["locked"]) == 3
    assert body["sampleCounts"] == {"serious": 1, "medium": 2, "small": 3}


async def test_zero_serious_and_one_medium_unlocks_one_medium_and_three_small(db):
    vid = await _vehicle(db, "FEW")
    await _comm(db, vid, "medium-0", "medium", "2090-02-01")
    for i in range(5):
        await _comm(db, vid, f"small-{i}", "small", f"2090-03-{i + 1:02d}")
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid)
    assert _unlocked(body) == {"medium-0", "small-4", "small-3", "small-2"}
    assert body["sampleCounts"] == {"serious": 0, "medium": 1, "small": 3}


async def test_revisions_fold_so_a_family_takes_one_slot(db):
    vid = await _vehicle(db, "FOLD")
    await _comm(db, vid, "serious-old", "serious", "2090-01-01", number="TSFOLD1")
    await _comm(db, vid, "serious-new", "serious", "2090-02-01", number="TSFOLD1A")
    await _comm(db, vid, "serious-other", "serious", "2090-01-15", number="TSFOLD2")
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid)
    assert body["total"] == 2
    assert _unlocked(body) == {"serious-new"}
    assert body["items"][0]["supersededCount"] == 1


async def test_active_unlock_for_the_vehicle_unlocks_everything_on_it(db):
    vid = await _seed(db)
    uid = await _link(db, USER_A, vid)
    await _entitle(db, uid, "unlock", vehicle_id=vid)
    body = await _get(USER_A, vid, perPage=200)
    assert body["access"] == "full"
    assert all(not i["locked"] for i in body["items"])
    assert body["sampleCounts"] == {"serious": 4, "medium": 5, "small": 7}


async def test_unlock_does_not_cover_another_vehicle(db):
    v1, v2 = await _seed(db, "M1"), await _seed(db, "M2")
    uid = await _link(db, USER_A, v1)
    await _link(db, USER_A, v2)
    await _entitle(db, uid, "unlock", vehicle_id=v1)
    assert (await _get(USER_A, v2))["access"] == "sample"


async def test_active_subscription_unlocks_every_vehicle(db):
    v1, v2 = await _seed(db, "M1"), await _seed(db, "M2")
    uid = await _link(db, USER_A, v1)
    await _link(db, USER_A, v2)
    await _entitle(db, uid, "subscription")
    for vid in (v1, v2):
        body = await _get(USER_A, vid, perPage=200)
        assert body["access"] == "full"
        assert all(not i["locked"] for i in body["items"])


@pytest.mark.parametrize("status", ["past_due", "canceled"])
@pytest.mark.parametrize("kind", ["unlock", "subscription"])
async def test_past_due_and_canceled_never_grant_full(db, kind, status):
    vid = await _seed(db)
    uid = await _link(db, USER_A, vid)
    await _entitle(db, uid, kind, status=status, vehicle_id=vid if kind == "unlock" else None)
    body = await _get(USER_A, vid, perPage=200)
    assert body["access"] == "sample"
    assert sum(1 for i in body["items"] if i["locked"]) == 10


async def test_an_admin_gets_full(db):
    vid = await _seed(db)
    await _link(db, ADMIN, vid)
    body = await _get(ADMIN, vid, perPage=200)
    assert body["access"] == "full"
    assert all(not i["locked"] for i in body["items"])


async def test_another_users_unlock_or_subscription_never_unlocks_for_me(db):
    vid = await _seed(db)
    a = await _link(db, USER_A, vid)
    await _link(db, USER_B, vid)
    await _entitle(db, a, "unlock", vehicle_id=vid)
    await _entitle(db, a, "subscription")
    assert (await _get(USER_A, vid))["access"] == "full"
    b = await _get(USER_B, vid, perPage=200)
    assert b["access"] == "sample"
    assert sum(1 for i in b["items"] if not i["locked"]) == 6


async def test_access_for_is_evaluated_for_the_caller_even_when_unlinked(db):
    vid = await _seed(db)
    a = await _link(db, USER_A, vid)
    b = await _link(db, USER_B, vid)
    await db.execute("DELETE FROM user_vehicles WHERE user_id = $1", b)
    await _entitle(db, a, "unlock", vehicle_id=vid)
    assert await access_for(db, a, vid) == "full"
    assert await access_for(db, b, vid) == "sample"
    assert await access_for(db, b, 999999999) == "sample"


async def test_me_vehicles_reports_the_access_of_each_vehicle(db):
    v1, v2 = await _seed(db, "M1"), await _seed(db, "M2")
    uid = await _link(db, USER_A, v1)
    await _link(db, USER_A, v2)
    await _entitle(db, uid, "unlock", vehicle_id=v1)
    async with _http() as c:
        me = await c.get("/api/me", headers=_as(USER_A))
    access = {v["vehicleId"]: v["access"] for v in me.json()["vehicles"]}
    assert access == {v1: "full", v2: "sample"}


async def test_search_matches_only_readable_rows_and_never_leaks_a_locked_one(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    # serious-0 is the oldest serious, so it is locked; give it a word nothing else has.
    await db.execute(
        "UPDATE communications SET summary = 'zebrafoo quirk' WHERE nhtsa_id = $1",
        PREFIX + "serious-0",
    )
    body = await _get(USER_A, vid, search="zebrafoo", perPage=200)
    assert body["items"] == [] and body["total"] == 0

    body = await _get(USER_A, vid, search=SECRET, perPage=200)
    assert body["total"] == 6
    assert all(not i["locked"] for i in body["items"])
    assert _unlocked(body) == {"serious-3", "medium-4", "medium-3", "small-6", "small-5", "small-4"}
    assert body["sampleCounts"] == {"serious": 1, "medium": 2, "small": 3}


async def test_search_with_full_access_searches_every_row(db):
    vid = await _seed(db)
    uid = await _link(db, USER_A, vid)
    await _entitle(db, uid, "unlock", vehicle_id=vid)
    await db.execute(
        "UPDATE communications SET summary = 'zebrafoo quirk' WHERE nhtsa_id = $1",
        PREFIX + "serious-0",
    )
    body = await _get(USER_A, vid, search="zebrafoo", perPage=200)
    assert [i["nhtsaId"] for i in body["items"]] == [PREFIX + "serious-0"]
    assert body["items"][0]["locked"] is False
