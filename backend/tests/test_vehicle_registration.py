"""Adding a vehicle in the app registers it with the pipeline (JIP-414).

The UI writes tracked vehicles to Mongo; the pipeline takes its worklist from
the Postgres vehicles table. Without a mirror, a vehicle added in the app is
invisible to sync forever, so its communications never reach the corpus and the
detail page stays empty while the card shows a healthy count.

Runs against both real databases, cleaning up after itself.
"""

import uuid

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs import repository as repo
from src.vehicles.service import VehicleService

# Far outside the real NHTSA id range so a stray row can never collide.
VID = 999_000_000 + int(uuid.uuid4().int % 100_000)


@pytest_asyncio.fixture
async def clean():
    """Both stores connected to this test's event loop, and no leftover rows.

    pytest-asyncio runs each test on a fresh loop while src.database memoises
    one Motor client, so a client built on an earlier loop raises "Event loop is
    closed". Reconnecting per test mirrors what conftest._reset_pool does for
    asyncpg.
    """
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    from src.database import close_mongodb_connection, connect_to_mongodb, get_database

    await close_mongodb_connection()
    try:
        await connect_to_mongodb()
    except Exception:
        pytest.skip("MongoDB not reachable")

    async def wipe():
        conn = await asyncpg.connect(settings.database_url)
        try:
            await conn.execute("DELETE FROM vehicles WHERE nhtsa_vehicle_id = $1", VID)
        finally:
            await conn.close()
        await get_database().vehicles.delete_one({"vehicle_id": VID})

    await wipe()
    yield
    await wipe()
    await close_mongodb_connection()


async def _pg_row():
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        return await conn.fetchrow(
            "SELECT nhtsa_vehicle_id, year, make, model, trim, active "
            "FROM vehicles WHERE nhtsa_vehicle_id = $1",
            VID,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_creating_a_vehicle_registers_it_with_the_pipeline(clean):
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", trim="SUV", keywords=[]
    )

    row = await _pg_row()
    assert row is not None, "the vehicle never reached the pipeline's worklist"
    assert row["active"] is True
    assert row["year"] == 2019
    assert row["model"] == "CX-5"


@pytest.mark.asyncio
async def test_the_real_make_is_stored_rather_than_inferred(clean):
    """import_vehicles_from_mongo guesses make from a hardcoded GM nameplate
    map, so anything else became UNKNOWN. The add flow already knows it."""
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", trim="SUV", keywords=[]
    )

    row = await _pg_row()
    assert row["make"] == "MAZDA"
    assert row["trim"] == "SUV"


@pytest.mark.asyncio
async def test_sync_sees_the_vehicle_without_an_import_bootstrap(clean):
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )

    conn = await asyncpg.connect(get_settings().database_url)
    try:
        tracked = {v["nhtsa_vehicle_id"] for v in await repo.tracked_vehicles(conn)}
    finally:
        await conn.close()

    assert VID in tracked


@pytest.mark.asyncio
async def test_deleting_a_vehicle_stops_the_pipeline_polling_it(clean):
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )

    await VehicleService.delete_vehicle(VID)

    row = await _pg_row()
    assert row is not None, "the row is kept; communication links hang off it"
    assert row["active"] is False


@pytest.mark.asyncio
async def test_re_adding_a_removed_vehicle_reactivates_it(clean):
    """tracked_vehicles filters on active, so an upsert that left the flag
    false would strand the vehicle permanently."""
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )
    await VehicleService.delete_vehicle(VID)

    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )

    assert (await _pg_row())["active"] is True


@pytest.mark.asyncio
async def test_a_pipeline_write_failure_does_not_block_the_add(clean, monkeypatch):
    """Postgres being down must not stop someone adding a vehicle."""

    async def boom(*a, **k):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr("src.db.get_pool", boom)

    result = await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )

    assert result["vehicle_id"] == VID
    assert await _pg_row() is None
