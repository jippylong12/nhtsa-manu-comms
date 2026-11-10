"""Search, unread, type, systems and date filters, read flag and tab facets on
GET /api/me/vehicles/{id}/bulletins (JIP-1603).

Only `firebase.verify` is faked; rows are real and TESTFILT-named.
Car: 4 serious, 5 medium, 7 small families; tag number = rank (higher = newer).
medium-3 and medium-4 mention "brake" in the LLM summary.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTFILT-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTFILTMAKE"
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


async def _comm(conn, vid, tag, severity, date, ctype="TSB", summary="plain text", systems=()):
    cid = await conn.fetchval(
        """
        INSERT INTO communications (nhtsa_id, communication_number, communication_type,
            communication_date, summary, status, severity)
        VALUES ($1, $2, $3, $4::text::date, 'seed', 'processed', $5) RETURNING id
        """,
        PREFIX + tag, "TF" + tag.replace("-", "") + "00", ctype, date, severity,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid, vid,
    )
    await conn.execute(
        "INSERT INTO comm_documents (communication_id, url, llm_summary, systems) "
        "VALUES ($1, $2, $3, $4::text[])",
        cid, f"https://example.test/{PREFIX}{tag}.pdf", summary, list(systems),
    )


async def _seed(conn) -> int:
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2033, $1, 'M1') RETURNING id", MAKE
    )
    for sev, n in (("serious", 4), ("medium", 5), ("small", 7)):
        for i in range(n):
            tag = f"{sev}-{i}"
            braky = tag in ("medium-3", "medium-4")
            await _comm(
                conn, vid, tag, sev, f"2090-01-{i + 1:02d}",
                ctype="PIT" if tag == "small-0" else "TSB",
                summary="Brake shudder under load" if braky else "Nothing relevant here",
                systems=("Hydraulics",) if tag in ("serious-0", "serious-1") else (),
            )
    return vid


async def _link(conn, uid: str, vid: int, unlock: bool = True) -> int:
    user_id = await conn.fetchval(
        """INSERT INTO users (firebase_uid, email) VALUES ($1, $2)
           ON CONFLICT (firebase_uid) DO UPDATE SET email = EXCLUDED.email RETURNING id""",
        uid, uid + "@example.com",
    )
    await conn.execute(
        "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2)", user_id, vid
    )
    if unlock:
        await conn.execute(
            "INSERT INTO entitlements (user_id, kind, vehicle_id, status) "
            "VALUES ($1, 'unlock', $2, 'active')",
            user_id, vid,
        )
    return user_id


async def _read(conn, user_id: int, *tags: str) -> None:
    for tag in tags:
        await conn.execute(
            "INSERT INTO bulletin_reads (user_id, nhtsa_id) VALUES ($1, $2)",
            user_id, PREFIX + tag,
        )


async def _get(uid, vid, expect=200, **params):
    async with _http() as c:
        r = await c.get(f"/api/me/vehicles/{vid}/bulletins", headers=_as(uid), params=params)
    assert r.status_code == expect, r.text
    return r.json()


def _tags(body) -> set[str]:
    return {i["nhtsaId"].removeprefix(PREFIX) for i in body["items"]}


async def test_search_returns_the_matches_and_facets_follow_the_search(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, search="brake", perPage=200)
    assert _tags(body) == {"medium-3", "medium-4"}
    assert body["total"] == 2
    assert body["facets"]["bySeverity"] == {"serious": 0, "medium": 2, "small": 0}


async def test_severity_narrows_the_rows_but_facets_cover_every_tier(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, severity="small", search="", perPage=200)
    assert len(body["items"]) == 7 and body["total"] == 7
    assert body["facets"]["bySeverity"] == {"serious": 4, "medium": 5, "small": 7}


async def test_unread_only_hides_read_rows_and_flags_read_on_every_row(db):
    vid = await _seed(db)
    uid = await _link(db, USER_A, vid)
    await _read(db, uid, "serious-3", "small-6")
    every = await _get(USER_A, vid, perPage=200)
    assert {i["nhtsaId"].removeprefix(PREFIX) for i in every["items"] if i["read"]} == {
        "serious-3", "small-6"
    }
    body = await _get(USER_A, vid, unreadOnly="true", perPage=200)
    assert len(body["items"]) == 14 and all(i["read"] is False for i in body["items"])
    assert sum(body["facets"]["unreadBySeverity"].values()) == 14
    assert sum(body["facets"]["bySeverity"].values()) == 16
    assert body["facets"]["unreadBySeverity"] == {"serious": 3, "medium": 5, "small": 6}


async def test_a_locked_row_still_carries_read_and_no_text(db):
    vid = await _seed(db)
    uid = await _link(db, USER_A, vid, unlock=False)
    await _read(db, uid, "serious-0", "serious-3")  # serious-0 is locked, serious-3 is free
    body = await _get(USER_A, vid, perPage=200)
    rows = {i["nhtsaId"].removeprefix(PREFIX): i for i in body["items"]}
    assert rows["serious-0"]["locked"] is True and rows["serious-0"]["read"] is True
    assert "llmSummary" not in rows["serious-0"] and "summary" not in rows["serious-0"]
    assert rows["serious-3"]["locked"] is False and rows["serious-3"]["read"] is True
    assert rows["serious-1"]["read"] is False


async def test_a_sample_user_searching_keeps_the_same_free_rows_unlocked(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid, unlock=False)
    plain = await _get(USER_A, vid, perPage=200)
    free = {i["nhtsaId"] for i in plain["items"] if not i["locked"]}
    body = await _get(USER_A, vid, search="brake", perPage=200)
    assert {i["nhtsaId"] for i in body["items"]} <= free
    assert _tags(body) == {"medium-3", "medium-4"}
    assert all(not i["locked"] and "read" in i for i in body["items"])


async def test_systems_filters_by_tag_ignoring_case_and_matching_any(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, systems="hydraulics,Nothing", perPage=200)
    assert _tags(body) == {"serious-0", "serious-1"}
    assert body["facets"]["bySeverity"] == {"serious": 2, "medium": 0, "small": 0}


async def test_comm_type_filters_by_the_type_code(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, commType="PIT", perPage=200)
    assert _tags(body) == {"small-0"}


async def test_date_from_and_to_bound_the_dates(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    body = await _get(
        USER_A, vid, severity="small", dateFrom="2090-01-03", dateTo="2090-01-05", perPage=200
    )
    assert _tags(body) == {"small-2", "small-3", "small-4"}
    assert body["facets"]["bySeverity"]["small"] == 3


async def test_nhtsa_id_returns_that_one_row_with_facets_or_nothing(db):
    vid = await _seed(db)
    other = await db.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2033, $1, 'M2') RETURNING id", MAKE
    )
    await _comm(db, other, "elsewhere-0", "serious", "2090-02-01")
    await _link(db, USER_A, vid)
    body = await _get(USER_A, vid, nhtsaId=PREFIX + "medium-2")
    assert _tags(body) == {"medium-2"} and body["total"] == 1
    assert sum(body["facets"]["bySeverity"].values()) == 16
    missing = await _get(USER_A, vid, nhtsaId=PREFIX + "elsewhere-0")
    assert missing["items"] == [] and missing["total"] == 0


async def test_another_users_vehicle_is_404(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    async with _http() as c:
        await c.get("/api/me", headers=_as(USER_B))  # creates the account, no car
    await _get(USER_B, vid, expect=404, search="brake")
