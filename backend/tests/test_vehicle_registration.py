"""/api/vehicles is served from Postgres alone, keyed on vehicles.id (JIP-1561).

A vehicle's identity is (year, make, model) with the model in canonical
plain-nameplate form (`F-150` is stored `F 150`); `vehicles.id` is the one id the
API speaks. There is no NHTSA vehicleId and no trim any more.

Runs against the real database, cleaning up after itself.
"""

import asyncio

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.auth import firebase
from src.communications import flatfile
from src.config import get_settings
from src.jobs import repository as repo
from src.vehicles import router as vehicles_router
from src.vehicles.service import VehicleService

# A vehicle no real tracked vehicle can be, so cleanup by identity is safe.
YEAR, MAKE, MODEL = 2019, "MAZDA", "CX 5"

_app = FastAPI()
_app.include_router(vehicles_router.router, prefix="/api")


ADMIN_UID = "TESTGATE-registration-admin"


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": "Bearer admin"},
    )


@pytest_asyncio.fixture(autouse=True)
async def signed_in_admin(monkeypatch):
    """The write routes need an admin; fake only the Firebase boundary."""
    monkeypatch.setenv("ADMIN_FIREBASE_UIDS", ADMIN_UID)
    get_settings.cache_clear()
    monkeypatch.setattr(firebase, "verify", lambda token: {"uid": ADMIN_UID, "email": "a@b.c"})
    yield
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute("DELETE FROM users WHERE firebase_uid = $1", ADMIN_UID)
    finally:
        await conn.close()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def scans(monkeypatch):
    """Record background scans so no test reaches NHTSA through the scan on add."""
    calls: list[dict] = []

    async def recorder(conn, *a, **kw):
        calls.append(kw)
        return {}

    monkeypatch.setattr(flatfile, "refresh_index", recorder)
    return calls


@pytest_asyncio.fixture
async def clean():
    """No leftover rows for the test identity, before or after."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        conn = await asyncpg.connect(settings.database_url)
        try:
            await conn.execute(
                "DELETE FROM vehicles WHERE year = $1 AND make = $2 AND model = $3",
                YEAR,
                MAKE,
                MODEL,
            )
            await conn.execute(
                "DELETE FROM vehicles WHERE year = 2021 AND make = 'FORD' AND model = 'F 150'"
            )
        finally:
            await conn.close()

    await wipe()
    yield
    await wipe()


async def _rows():
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        return await conn.fetch(
            "SELECT id, year, make, model, keywords, active FROM vehicles "
            "WHERE year = $1 AND make = $2 AND model = $3 ORDER BY id",
            YEAR,
            MAKE,
            MODEL,
        )
    finally:
        await conn.close()


async def _create(keywords=None, model="CX-5"):
    return await VehicleService.create_vehicle(
        year="2019", make="Mazda", model=model, keywords=keywords or []
    )


# --- R2: create contract -------------------------------------------------


@pytest.mark.asyncio
async def test_creating_a_vehicle_stores_the_canonical_identity(clean):
    created = await _create()

    (row,) = await _rows()
    assert row["active"] is True
    assert (row["year"], row["make"], row["model"]) == (YEAR, MAKE, MODEL)
    assert created["vehicle_id"] == row["id"] == int(created["id"])


@pytest.mark.asyncio
async def test_post_normalises_make_and_model_and_ignores_legacy_fields(clean):
    async with _http() as client:
        r = await client.post(
            "/api/vehicles",
            json={
                "year": "2021",
                "make": "Ford",
                "model": "F-150",
                "keywords": [],
                "vehicleId": 123,
                "trim": "SUPER CREW",
            },
        )

    assert r.status_code == 201
    body = r.json()
    assert body["make"] == "FORD"
    assert body["model"] == "F 150"
    assert body["vehicleId"] == int(body["id"]) != 123
    assert "trim" not in body


@pytest.mark.asyncio
async def test_post_without_a_make_is_rejected(clean):
    async with _http() as client:
        r = await client.post("/api/vehicles", json={"year": "2021", "model": "F-150"})

    assert r.status_code == 422


# --- R3: idempotent add --------------------------------------------------


@pytest.mark.asyncio
async def test_adding_the_same_identity_again_returns_the_same_vehicle(clean):
    first = await _create(model="CX-5")
    again = await _create(model="cx 5")

    assert again["vehicle_id"] == first["vehicle_id"]
    assert len(await _rows()) == 1


@pytest.mark.asyncio
async def test_a_racing_add_of_the_same_new_identity_reuses_the_winner(clean):
    """Two adds of a new identity at once: the loser must get the winner's row, not a 500.

    The winner's insert is held uncommitted so the loser's SELECT misses it and
    its INSERT blocks on the partial unique index; committing the winner then
    makes that INSERT fail with a unique violation, which upsert_vehicle must
    absorb.
    """
    url = get_settings().database_url
    winner = await asyncpg.connect(url)
    observer = await asyncpg.connect(url)
    try:
        tx = winner.transaction()
        await tx.start()
        winner_id = await winner.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
            YEAR,
            MAKE,
            MODEL,
        )
        loser = asyncio.create_task(_create())
        for _ in range(200):
            blocked = await observer.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                "AND query LIKE '%INSERT INTO vehicles%'"
            )
            if blocked or loser.done():
                break
            await asyncio.sleep(0.025)
        await tx.commit()

        created = await asyncio.wait_for(loser, timeout=10)
    finally:
        await winner.close()
        await observer.close()

    assert created["vehicle_id"] == winner_id
    assert len(await _rows()) == 1


# --- R4: reactivation ----------------------------------------------------


@pytest.mark.asyncio
async def test_re_adding_a_removed_vehicle_revives_the_same_row_and_keeps_keywords(clean):
    first = await _create(keywords=["battery"])
    assert await VehicleService.delete_vehicle(first["vehicle_id"]) is True

    again = await _create(keywords=[])

    assert again["vehicle_id"] == first["vehicle_id"]
    (row,) = await _rows()
    assert row["active"] is True
    assert list(row["keywords"]) == ["battery"]
    assert await VehicleService.get_vehicle(first["vehicle_id"]) is not None


@pytest.mark.asyncio
async def test_re_adding_with_keywords_replaces_them(clean):
    first = await _create(keywords=["battery"])
    await VehicleService.delete_vehicle(first["vehicle_id"])

    await _create(keywords=["brakes"])

    (row,) = await _rows()
    assert list(row["keywords"]) == ["brakes"]


# --- R5: unique identity -------------------------------------------------


@pytest.mark.asyncio
async def test_two_active_vehicles_cannot_share_an_identity(conn):
    await conn.execute(
        "INSERT INTO vehicles (year, make, model) VALUES (2021, 'FORD', 'F 150')"
    )
    with pytest.raises(asyncpg.UniqueViolationError) as caught:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO vehicles (year, make, model) VALUES (2021, 'FORD', 'F 150')"
            )
    assert caught.value.constraint_name == "vehicles_active_identity_uidx"

    # A soft-deleted twin is allowed: the index is partial.
    await conn.execute(
        "INSERT INTO vehicles (year, make, model, active) VALUES (2021, 'FORD', 'F 150', false)"
    )


# --- the vehicle is addressed by its row id ------------------------------


@pytest.mark.asyncio
async def test_sync_sees_the_vehicle_without_an_import_bootstrap(clean):
    created = await _create()

    conn = await asyncpg.connect(get_settings().database_url)
    try:
        tracked = {v["id"] for v in await repo.tracked_vehicles(conn)}
    finally:
        await conn.close()

    assert created["vehicle_id"] in tracked


@pytest.mark.asyncio
async def test_deleting_a_vehicle_stops_the_pipeline_polling_it(clean):
    created = await _create()

    assert await VehicleService.delete_vehicle(created["vehicle_id"]) is True

    (row,) = await _rows()
    assert row["active"] is False, "the row is kept; communication links hang off it"


@pytest.mark.asyncio
async def test_a_deleted_vehicle_is_gone_from_the_api(clean):
    """Soft delete is an implementation detail. From outside it is a delete."""
    created = await _create()
    vid = created["vehicle_id"]
    await VehicleService.delete_vehicle(vid)

    assert await VehicleService.get_vehicle(vid) is None
    assert await VehicleService.delete_vehicle(vid) is False, "a second delete is a 404"

    items, _ = await VehicleService.list_vehicles(per_page=200)
    assert vid not in {v["vehicle_id"] for v in items}


@pytest.mark.asyncio
async def test_the_api_reads_and_writes_one_store(clean):
    """What create returns, get returns, and list contains are the same row."""
    created = await _create(keywords=["battery"])
    fetched = await VehicleService.get_vehicle(created["vehicle_id"])
    items, total = await VehicleService.list_vehicles(per_page=200)
    listed = next(v for v in items if v["vehicle_id"] == created["vehicle_id"])

    assert created == fetched == listed
    assert total >= 1
    assert created["year"] == "2019", "year stays a string over the wire"
    assert created["id"].isdigit(), "id is the canonical row id"


@pytest.mark.asyncio
async def test_get_by_id_over_http_resolves_the_vehicle(clean):
    created = await _create()
    async with _http() as client:
        found = await client.get(f"/api/vehicles/{created['vehicle_id']}")
        missing = await client.get("/api/vehicles/999999999")

    assert found.status_code == 200
    assert found.json()["vehicleId"] == created["vehicle_id"]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_comm_count_is_derived_not_stored(clean):
    """The count follows the corpus. A stored copy is what let a card read 389
    above a detail page reading 0."""
    created = await _create()
    vid = created["vehicle_id"]
    assert (await VehicleService.get_vehicle(vid))["comm_count"] == 0

    conn = await asyncpg.connect(get_settings().database_url)
    try:
        nhtsa_id = f"test-{vid}"
        comm_id = await conn.fetchval(
            "INSERT INTO communications (nhtsa_id) VALUES ($1) RETURNING id", nhtsa_id
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            comm_id,
            vid,
        )
        try:
            assert (await VehicleService.get_vehicle(vid))["comm_count"] == 1
        finally:
            await conn.execute("DELETE FROM communications WHERE nhtsa_id = $1", nhtsa_id)
    finally:
        await conn.close()


# --- R8: PATCH -----------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_updates_keywords_and_ignores_identity_fields(clean):
    created = await _create(keywords=[])
    vid = created["vehicle_id"]

    async with _http() as client:
        r = await client.patch(f"/api/vehicles/{vid}", json={"keywords": ["brakes"], "model": "X"})

    assert r.status_code == 200, "an identity field in the body is a no-op, not an error"
    body = r.json()
    assert body["keywords"] == ["brakes"]
    assert body["model"] == "CX 5"
    assert body["make"] == "MAZDA"


@pytest.mark.asyncio
async def test_patch_of_an_unknown_or_inactive_vehicle_is_404(clean):
    created = await _create()
    await VehicleService.delete_vehicle(created["vehicle_id"])

    async with _http() as client:
        inactive = await client.patch(f"/api/vehicles/{created['vehicle_id']}", json={"keywords": []})
        unknown = await client.patch("/api/vehicles/999999999", json={"keywords": []})

    assert inactive.status_code == 404
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_a_non_numeric_year_is_rejected(clean):
    """year is a string over the wire and an integer column."""
    with pytest.raises(ValueError):
        await VehicleService.create_vehicle(year="twenty", make="Mazda", model="CX-5")

    assert await _rows() == []


# --- R6: scan on add -----------------------------------------------------


@pytest.mark.asyncio
async def test_post_returns_201_then_a_background_scan_runs_for_that_vehicle(clean, scans):
    async with _http() as client:
        r = await client.post(
            "/api/vehicles", json={"year": "2019", "make": "Mazda", "model": "CX-5"}
        )
        assert r.status_code == 201
        assert scans == []  # the response did not wait for the scan
        for _ in range(20):
            if scans:
                break
            await asyncio.sleep(0.05)

    assert scans == [{"vehicles": [int(r.json()["id"])]}]


@pytest.mark.asyncio
async def test_a_failing_background_scan_does_not_fail_the_add(clean, monkeypatch, caplog):
    attempted = asyncio.Event()

    async def boom(conn, *a, **kw):
        attempted.set()
        raise RuntimeError("static.nhtsa.gov unreachable")

    monkeypatch.setattr(flatfile, "refresh_index", boom)

    async with _http() as client:
        r = await client.post(
            "/api/vehicles", json={"year": "2019", "make": "Mazda", "model": "CX-5"}
        )
        await asyncio.wait_for(attempted.wait(), 2)
        await asyncio.sleep(0.05)

    assert r.status_code == 201
    assert any("background scan" in rec.getMessage() for rec in caplog.records)
