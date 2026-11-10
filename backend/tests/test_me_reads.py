"""Per-account read state: bulletin_reads, read endpoints, unread and newThisWeek (JIP-1597).

`firebase.verify` and `refresh.service.start` are the only faked boundaries. Runs
against the real database on committed connections and deletes its own TESTRD rows.
"""

import itertools
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app
from src.refresh import service as refresh_service

pytestmark = pytest.mark.asyncio

PREFIX = "TESTRD-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTRDMAKE"
YEAR = 2033
_app = create_app()
_seq = itertools.count(1)


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


@pytest.fixture
def fakes(monkeypatch):
    async def fake_start(vehicle_id: int):
        return {"id": 1}

    monkeypatch.setattr(refresh_service, "start", fake_start)
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )


@pytest_asyncio.fixture
async def db(fakes):
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


async def _vehicle(conn, model: str) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
        YEAR, MAKE, model,
    )


async def _family(conn, vid: int, severity: str, days_ago: int = 1, revisions: int = 1) -> str:
    """One processed family on the vehicle; returns the nhtsa_id of its latest revision."""
    n = next(_seq)
    stem = f"{9000000 + n}"
    latest = ""
    for rev in range(1, revisions + 1):
        latest = f"{PREFIX}{n}-{rev}"
        cid = await conn.fetchval(
            """
            INSERT INTO communications (nhtsa_id, communication_number, communication_type,
                communication_date, summary, status, severity)
            VALUES ($1, $2, 'TSB', $3, 'seed', 'processed', $4) RETURNING id
            """,
            latest,
            f"{stem}-0{rev}",
            datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=-rev),
            severity,
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            cid, vid,
        )
    return latest


async def _link(c, conn, uid: str, model: str) -> None:
    """Add the (already seeded) vehicle to the account through the public endpoint."""
    r = await c.post(
        "/api/me/vehicles", headers=_as(uid), json={"year": YEAR, "make": MAKE, "model": model}
    )
    assert r.status_code == 201


async def _me_vehicle(c, uid: str) -> dict:
    return (await c.get("/api/me", headers=_as(uid))).json()["vehicles"][0]


async def _reads(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM bulletin_reads br JOIN users u ON u.id = br.user_id "
        "WHERE u.firebase_uid LIKE $1",
        PREFIX + "%",
    )


async def test_reading_twice_is_204_with_one_row_and_unread_drops_by_one(db):
    vid = await _vehicle(db, "A1")
    nid = await _family(db, vid, "serious")
    await _family(db, vid, "serious")
    async with _http() as c:
        await _link(c, db, USER_A, "A1")
        before = await _me_vehicle(c, USER_A)
        first = await c.post(f"/api/me/bulletins/{nid}/read", headers=_as(USER_A))
        second = await c.post(f"/api/me/bulletins/{nid}/read", headers=_as(USER_A))
        after = await _me_vehicle(c, USER_A)
    assert before["unread"] == {"serious": 2, "medium": 0, "small": 0}
    assert (first.status_code, second.status_code) == (204, 204)
    assert await _reads(db) == 1
    assert after["unread"] == {"serious": 1, "medium": 0, "small": 0}
    assert after["counts"]["serious"] == 2


async def test_a_bulletin_on_another_users_car_is_404_and_writes_nothing(db):
    vid = await _vehicle(db, "A2")
    nid = await _family(db, vid, "medium")
    other = await _vehicle(db, "B2")
    await _family(db, other, "medium")
    async with _http() as c:
        await _link(c, db, USER_A, "A2")
        await _link(c, db, USER_B, "B2")
        r = await c.post(f"/api/me/bulletins/{nid}/read", headers=_as(USER_B))
        ra = await c.post(f"/api/me/vehicles/{vid}/read-all", headers=_as(USER_B))
    assert r.status_code == 404
    assert ra.status_code == 404
    assert await _reads(db) == 0


async def test_read_all_with_severity_marks_only_that_severity(db):
    vid = await _vehicle(db, "A3")
    for _ in range(3):
        await _family(db, vid, "small")
    for _ in range(2):
        await _family(db, vid, "medium")
    async with _http() as c:
        await _link(c, db, USER_A, "A3")
        r = await c.post(f"/api/me/vehicles/{vid}/read-all?severity=small", headers=_as(USER_A))
        again = await c.post(f"/api/me/vehicles/{vid}/read-all?severity=small", headers=_as(USER_A))
        me = await _me_vehicle(c, USER_A)
    assert r.status_code == 200 and r.json() == {"marked": 3}
    assert again.json() == {"marked": 0}
    assert me["unread"] == {"serious": 0, "medium": 2, "small": 0}


async def test_new_this_week_counts_a_family_from_6_days_ago_not_8(db):
    vid = await _vehicle(db, "A4")
    await _family(db, vid, "serious", days_ago=2)
    await _family(db, vid, "medium", days_ago=6)
    await _family(db, vid, "small", days_ago=8)
    async with _http() as c:
        await _link(c, db, USER_A, "A4")
        me = await _me_vehicle(c, USER_A)
    assert me["newThisWeek"] == 2


async def test_a_family_with_two_revisions_counts_once_and_keys_on_the_latest(db):
    vid = await _vehicle(db, "A5")
    latest = await _family(db, vid, "serious", revisions=2)
    async with _http() as c:
        await _link(c, db, USER_A, "A5")
        me = await _me_vehicle(c, USER_A)
        await c.post(f"/api/me/bulletins/{latest}/read", headers=_as(USER_A))
        after = await _me_vehicle(c, USER_A)
    assert me["unread"]["serious"] == 1
    assert me["newThisWeek"] == 1
    assert after["unread"]["serious"] == 0
