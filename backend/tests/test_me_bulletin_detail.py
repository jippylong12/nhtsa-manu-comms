"""GET /api/me/vehicles/{id}/bulletins/{nhtsa_id}: one bulletin, under the sample rule.

This replaces the unrestricted corpus detail route for product pages. A readable
row (entitled, or inside the free sample) returns the full detail; a row the
sample rule would lock returns identity fields only. Only `firebase.verify` is
faked; rows are real and TESTBDET-named.
"""

import json

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTBDET-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTBDETMAKE"
SECRET = "SECRETSUMMARYTEXT"
LOCKED_KEYS = {"nhtsaId", "communicationType", "communicationDate", "severity", "locked"}
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


async def _vehicle(conn, model: str) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2033, $1, $2) RETURNING id", MAKE, model
    )


async def _comm(conn, vid, tag, severity, date):
    cid = await conn.fetchval(
        """
        INSERT INTO communications (nhtsa_id, communication_number, communication_type,
            communication_date, summary, status, severity)
        VALUES ($1, $2, 'TSB', $3::text::date, $4, 'processed', $5) RETURNING id
        """,
        PREFIX + tag, "TS" + tag.replace("-", "") + "00", date, SECRET, severity,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid, vid,
    )
    await conn.execute(
        """
        INSERT INTO comm_documents (communication_id, url, llm_summary, symptoms, systems,
            components, remedy, applicability, applicability_conditions, audience)
        VALUES ($1, $2, $3, ARRAY['SECRETSYMPTOM'], ARRAY['SECRETSYSTEM'],
            ARRAY['SECRETCOMPONENT'], 'SECRETREMEDY', 'SECRETAPPLIC', 'SECRETCOND', 'owner')
        """,
        cid, f"https://example.test/{PREFIX}{tag}.pdf", SECRET,
    )


async def _seed(conn, model="M1") -> int:
    """Serious: two rows, `serious-new` is inside the sample and `serious-old` is locked."""
    vid = await _vehicle(conn, model)
    await _comm(conn, vid, "serious-old", "serious", "2090-01-01")
    await _comm(conn, vid, "serious-new", "serious", "2090-01-02")
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


async def _get(uid, vid, tag):
    async with _http() as c:
        return await c.get(
            f"/api/me/vehicles/{vid}/bulletins/{PREFIX}{tag}",
            headers=_as(uid) if uid else None,
        )


async def test_a_row_inside_the_sample_returns_the_full_detail(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    r = await _get(USER_A, vid, "serious-new")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nhtsaId"] == PREFIX + "serious-new"
    assert body["summary"] == SECRET
    assert body["severity"] == "serious"
    assert body["locked"] is False
    assert body["documents"][0]["remedy"] == "SECRETREMEDY"
    assert body["documents"][0]["symptoms"] == ["SECRETSYMPTOM"]
    assert body["vehicles"][0]["vehicleId"] == vid


async def test_a_row_the_sample_locks_returns_identity_fields_only(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    r = await _get(USER_A, vid, "serious-old")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == LOCKED_KEYS
    assert body["locked"] is True
    assert body["nhtsaId"] == PREFIX + "serious-old"
    for text in ("SECRET", "summary", "remedy", "symptoms", "documents", "applicab"):
        assert text.lower() not in json.dumps(body).lower()


async def test_an_entitled_user_reads_the_locked_row_in_full(db):
    vid = await _seed(db)
    user_id = await _link(db, USER_A, vid)
    await db.execute(
        "INSERT INTO entitlements (user_id, kind, vehicle_id, status) "
        "VALUES ($1, 'unlock', $2, 'active')",
        user_id, vid,
    )
    r = await _get(USER_A, vid, "serious-old")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locked"] is False
    assert body["summary"] == SECRET
    assert body["documents"][0]["remedy"] == "SECRETREMEDY"


async def test_another_users_vehicle_is_404(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    await _link(db, USER_B, await _vehicle(db, "OTHER"))
    r = await _get(USER_B, vid, "serious-new")
    assert r.status_code == 404
    assert SECRET not in r.text


async def test_an_nhtsa_id_that_is_not_a_bulletin_of_the_vehicle_is_404(db):
    vid = await _seed(db)
    other = await _vehicle(db, "OTHER")
    await _comm(db, other, "elsewhere", "serious", "2090-03-01")
    await _link(db, USER_A, vid)
    assert (await _get(USER_A, vid, "elsewhere")).status_code == 404
    assert (await _get(USER_A, vid, "does-not-exist")).status_code == 404


async def _newest_revision_pending(conn, vid) -> None:
    """A newer revision of `serious-new` (same number stem plus `A`) still pending.

    Symptom search resolves a match to the newest revision of its family across every
    status, so this is the id its card shows, while the processed-only fold the bulletin
    list uses never contains it.
    """
    cid = await conn.fetchval(
        """
        INSERT INTO communications (nhtsa_id, communication_number, communication_type,
            communication_date, summary, status, severity)
        VALUES ($1, 'TSseriousnew00A', 'TSB', '2090-02-01', $2, 'pending', 'serious')
        RETURNING id
        """,
        PREFIX + "serious-new-rev", SECRET,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid, vid,
    )


async def test_an_entitled_reader_opens_a_symptom_match_outside_the_processed_fold(db):
    vid = await _seed(db)
    user_id = await _link(db, USER_A, vid)
    await db.execute(
        "INSERT INTO entitlements (user_id, kind, status) VALUES ($1, 'subscription', 'active')",
        user_id,
    )
    await _newest_revision_pending(db, vid)
    r = await _get(USER_A, vid, "serious-new-rev")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locked"] is False
    assert body["nhtsaId"] == PREFIX + "serious-new-rev"
    assert body["status"] == "pending"


async def test_a_sample_reader_cannot_open_a_row_outside_the_processed_fold(db):
    vid = await _seed(db)
    await _link(db, USER_A, vid)
    await _newest_revision_pending(db, vid)
    r = await _get(USER_A, vid, "serious-new-rev")
    assert r.status_code == 404
    assert SECRET not in r.text
    # The superseded, locked revision stays locked rather than opening through this path.
    assert set((await _get(USER_A, vid, "serious-old")).json()) == LOCKED_KEYS


async def test_anonymous_is_401(db):
    vid = await _seed(db)
    r = await _get(None, vid, "serious-new")
    assert r.status_code == 401
