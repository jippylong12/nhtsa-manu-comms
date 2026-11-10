"""/api/me/vehicles: link, create-and-ingest, unlink, per-user isolation (JIP-1587).

`firebase.verify` and `refresh.queue.request` are the only faked boundaries. Runs
against the real database on committed connections and deletes its own TESTME rows.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.config import get_settings
from src.main import create_app
from src.refresh import queue as ingest_queue

pytestmark = pytest.mark.asyncio

PREFIX = "TESTME-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTMEMAKE"
YEAR = 2032
_app = create_app()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


@pytest.fixture
def refresh_calls(monkeypatch):
    calls: list[int] = []

    async def fake_start(vehicle_id: int):
        calls.append(vehicle_id)
        return {"id": 1}

    monkeypatch.setattr(ingest_queue, "request", fake_start)
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )
    return calls


@pytest_asyncio.fixture
async def db(refresh_calls):
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


async def _vehicle(conn, model: str, processed: int = 0) -> int:
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
        YEAR, MAKE, model,
    )
    for i in range(processed):
        cid = await conn.fetchval(
            """
            INSERT INTO communications (nhtsa_id, communication_number, communication_type,
                communication_date, summary, status, severity)
            VALUES ($1, $2, 'TSB', '2099-01-01', 'seed summary', 'processed', 'serious')
            RETURNING id
            """,
            f"{PREFIX}{model}-{i}", f"TMV{model}{i}00",
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            cid, vid,
        )
    return vid


async def _user(conn, uid: str) -> int:
    return await conn.fetchval(
        """INSERT INTO users (firebase_uid, email) VALUES ($1, $2)
           ON CONFLICT (firebase_uid) DO UPDATE SET email = EXCLUDED.email RETURNING id""",
        uid, uid + "@example.com",
    )


async def _subscribe(conn, uid: str) -> None:
    await conn.execute(
        "INSERT INTO entitlements (user_id, kind, status) VALUES ($1, 'subscription', 'active')",
        await _user(conn, uid),
    )


def _body(model: str) -> dict:
    return {"year": YEAR, "make": MAKE, "model": model}


async def test_adding_a_tracked_processed_vehicle_links_it_ready_without_refresh(db, refresh_calls):
    vid = await _vehicle(db, "CIVIC", processed=2)
    async with _http() as c:
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CIVIC"))
        me = await c.get("/api/me", headers=_as(USER_A))
    assert r.status_code == 201
    body = r.json()
    assert body["vehicleId"] == vid
    assert body["availability"] == "ready"
    assert body["counts"] == {"serious": 2, "medium": 0, "small": 0}
    assert body["access"] == "sample"
    assert body["addedAt"]
    assert refresh_calls == []
    assert [v["vehicleId"] for v in me.json()["vehicles"]] == [vid]


async def test_adding_an_unknown_identity_creates_it_and_queues_one_read(db, refresh_calls):
    async with _http() as c:
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("NEWCAR"))
    assert r.status_code == 201
    assert r.json()["availability"] == "processing"
    vid = await db.fetchval("SELECT id FROM vehicles WHERE make = $1 AND model = 'NEWCAR'", MAKE)
    assert vid == r.json()["vehicleId"]
    assert refresh_calls == [vid]


async def test_adding_twice_is_idempotent(db):
    await _vehicle(db, "CIVIC", processed=1)
    async with _http() as c:
        await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CIVIC"))
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CIVIC"))
        me = await c.get("/api/me", headers=_as(USER_A))
    assert r.status_code == 201
    assert len(me.json()["vehicles"]) == 1


async def test_the_eleventh_vehicle_is_409(db):
    await _subscribe(db, USER_A)  # only a subscriber can reach ten cars
    async with _http() as c:
        for i in range(10):
            r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body(f"CAR{i}"))
            assert r.status_code == 201
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CAR10"))
        again = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CAR3"))
    assert r.status_code == 409
    assert again.status_code == 201  # re-adding a linked vehicle is not an eleventh


async def test_anonymous_is_401(db):
    async with _http() as c:
        assert (await c.post("/api/me/vehicles", json=_body("X"))).status_code == 401
        assert (await c.get("/api/me/vehicles/1/bulletins")).status_code == 401
        assert (await c.delete("/api/me/vehicles/1")).status_code == 401


async def test_unlink_removes_the_link_and_bulletins_become_404(db):
    vid = await _vehicle(db, "CIVIC", processed=1)
    async with _http() as c:
        await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CIVIC"))
        ok = await c.get(f"/api/me/vehicles/{vid}/bulletins", headers=_as(USER_A))
        d = await c.delete(f"/api/me/vehicles/{vid}", headers=_as(USER_A))
        gone = await c.get(f"/api/me/vehicles/{vid}/bulletins", headers=_as(USER_A))
        d2 = await c.delete(f"/api/me/vehicles/{vid}", headers=_as(USER_A))
    assert ok.status_code == 200
    assert d.status_code == 204
    assert gone.status_code == 404
    assert d2.status_code == 404
    # the vehicle itself stays tracked
    assert await db.fetchval("SELECT count(*) FROM vehicles WHERE id = $1", vid) == 1


async def test_another_users_vehicle_is_404_identical_to_a_missing_one(db):
    vid = await _vehicle(db, "CIVIC", processed=1)
    async with _http() as c:
        await c.post("/api/me/vehicles", headers=_as(USER_B), json=_body("CIVIC"))
        theirs = await c.get(f"/api/me/vehicles/{vid}/bulletins", headers=_as(USER_A))
        missing = await c.get("/api/me/vehicles/999999999/bulletins", headers=_as(USER_A))
        del_theirs = await c.delete(f"/api/me/vehicles/{vid}", headers=_as(USER_A))
        del_missing = await c.delete("/api/me/vehicles/999999999", headers=_as(USER_A))
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()
    assert del_theirs.status_code == del_missing.status_code == 404
    assert del_theirs.json() == del_missing.json()


async def test_unlinking_and_adding_never_touch_another_users_links(db):
    vid = await _vehicle(db, "CIVIC", processed=1)
    other = await _vehicle(db, "ACCORD", processed=1)
    async with _http() as c:
        await c.post("/api/me/vehicles", headers=_as(USER_B), json=_body("CIVIC"))
        await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("CIVIC"))
        await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("ACCORD"))
        await c.delete(f"/api/me/vehicles/{vid}", headers=_as(USER_B))
        a = await c.get("/api/me", headers=_as(USER_A))
        # B tries to unlink A's ACCORD: 404, A's link remains
        r = await c.delete(f"/api/me/vehicles/{other}", headers=_as(USER_B))
        a2 = await c.get("/api/me", headers=_as(USER_A))
        b = await c.get("/api/me", headers=_as(USER_B))
    assert {v["vehicleId"] for v in a.json()["vehicles"]} == {vid, other}
    assert r.status_code == 404
    assert {v["vehicleId"] for v in a2.json()["vehicles"]} == {vid, other}
    assert b.json()["vehicles"] == []


async def test_a_user_id_in_the_request_is_ignored(db):
    await _vehicle(db, "CIVIC", processed=1)
    async with _http() as c:
        await c.post(
            "/api/me/vehicles?userId=1", headers=_as(USER_A),
            json={**_body("CIVIC"), "userId": 1},
        )
        me_a = await c.get("/api/me", headers=_as(USER_A))
        me_b = await c.get("/api/me", headers=_as(USER_B))
    assert len(me_a.json()["vehicles"]) == 1
    assert me_b.json()["vehicles"] == []


async def test_a_capped_account_cannot_create_vehicles_by_collecting_409s(db, refresh_calls):
    await _subscribe(db, USER_A)
    async with _http() as c:
        for i in range(10):
            assert (
                await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body(f"CAR{i}"))
            ).status_code == 201
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("OVERCAP"))
    assert r.status_code == 409
    # The refused identity was never created, so the nightly pipeline has nothing to ingest.
    assert await db.fetchval(
        "SELECT count(*) FROM vehicles WHERE make = $1 AND model = 'OVERCAP'", MAKE
    ) == 0
    assert len(refresh_calls) == 10


async def test_read_only_mode_links_a_new_vehicle_without_starting_a_refresh(
    db, refresh_calls, monkeypatch
):
    monkeypatch.setattr(get_settings(), "read_only", True)
    async with _http() as c:
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("ROCAR"))
    assert r.status_code == 201
    assert r.json()["availability"] == "processing"
    assert refresh_calls == []


async def _add(c, uid: str, model: str) -> int:
    return (await c.post("/api/me/vehicles", headers=_as(uid), json=_body(model))).status_code


async def test_a_free_account_gets_two_sample_cars_and_the_third_is_refused(db, refresh_calls):
    async with _http() as c:
        assert await _add(c, USER_A, "ONE") == 201
        assert await _add(c, USER_A, "TWO") == 201
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("THREE"))
    assert r.status_code == 402
    assert "2 free samples" in r.json()["detail"]
    # The refused car was never created, so nothing is queued or read for it.
    assert await db.fetchval(
        "SELECT count(*) FROM vehicles WHERE make = $1 AND model = 'THREE'", MAKE
    ) == 0
    assert len(refresh_calls) == 2


async def test_removing_a_sample_car_does_not_hand_out_a_new_sample(db):
    async with _http() as c:
        assert await _add(c, USER_A, "ONE") == 201
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("TWO"))
        assert r.status_code == 201
        await c.delete(f"/api/me/vehicles/{r.json()['vehicleId']}", headers=_as(USER_A))
        assert await _add(c, USER_A, "THREE") == 402
        # The car that was removed can come back: it was already one of the two.
        assert await _add(c, USER_A, "TWO") == 201


async def test_unlocking_a_car_frees_its_sample_slot(db):
    async with _http() as c:
        r = await c.post("/api/me/vehicles", headers=_as(USER_A), json=_body("ONE"))
        assert await _add(c, USER_A, "TWO") == 201
        assert await _add(c, USER_A, "THREE") == 402
        await db.execute(
            "INSERT INTO entitlements (user_id, kind, vehicle_id, status) "
            "VALUES ($1, 'unlock', $2, 'active')",
            await _user(db, USER_A), r.json()["vehicleId"],
        )
        assert await _add(c, USER_A, "THREE") == 201
        assert await _add(c, USER_A, "FOUR") == 402


async def test_a_subscriber_is_not_held_to_two_cars(db):
    await _subscribe(db, USER_A)
    async with _http() as c:
        for model in ("ONE", "TWO", "THREE", "FOUR"):
            assert await _add(c, USER_A, model) == 201


async def test_one_accounts_samples_do_not_count_against_another(db):
    async with _http() as c:
        assert await _add(c, USER_A, "ONE") == 201
        assert await _add(c, USER_A, "TWO") == 201
        assert await _add(c, USER_B, "ONE") == 201
        assert await _add(c, USER_B, "THREE") == 201
