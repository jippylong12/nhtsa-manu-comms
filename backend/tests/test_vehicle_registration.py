"""/api/vehicles is served from Postgres alone (JIP-416).

The predecessor of this file proved that a vehicle added in the app was mirrored
from Mongo into the Postgres table the pipeline reads (JIP-414). There is no
mirror any more: the app writes the pipeline's own table, so what needs proving
has shifted from "both stores agree" to "one store answers every question", plus
the two behaviours the mirror got wrong and must not regress.

Runs against the real database, cleaning up after itself.
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
    """No leftover rows for VID, before or after."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        conn = await asyncpg.connect(settings.database_url)
        try:
            await conn.execute("DELETE FROM vehicles WHERE nhtsa_vehicle_id = $1", VID)
        finally:
            await conn.close()

    await wipe()
    yield
    await wipe()


async def _pg_row():
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        return await conn.fetchrow(
            "SELECT nhtsa_vehicle_id, year, make, model, trim, keywords, active "
            "FROM vehicles WHERE nhtsa_vehicle_id = $1",
            VID,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_creating_a_vehicle_writes_the_canonical_table(clean):
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", trim="SUV", keywords=[]
    )

    row = await _pg_row()
    assert row is not None, "the vehicle never reached the canonical store"
    assert row["active"] is True
    assert row["year"] == 2019
    assert row["model"] == "CX-5"


@pytest.mark.asyncio
async def test_the_real_make_is_stored_rather_than_inferred(clean):
    """import_vehicles_from_mongo guessed make from a hardcoded GM nameplate
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

    assert await VehicleService.delete_vehicle(VID) is True

    row = await _pg_row()
    assert row is not None, "the row is kept; communication links hang off it"
    assert row["active"] is False


@pytest.mark.asyncio
async def test_a_deleted_vehicle_is_gone_from_the_api(clean):
    """Soft delete is an implementation detail. From outside it is a delete."""
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )
    await VehicleService.delete_vehicle(VID)

    assert await VehicleService.get_vehicle(VID) is None
    assert await VehicleService.delete_vehicle(VID) is False, "a second delete is a 404"

    items, _ = await VehicleService.list_vehicles(per_page=200)
    assert VID not in {v["vehicle_id"] for v in items}


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
    assert await VehicleService.get_vehicle(VID) is not None


@pytest.mark.asyncio
async def test_re_adding_does_not_erase_what_the_caller_omitted(clean):
    """The add-vehicle form can submit without a make or keywords. Before, the
    upsert took EXCLUDED unconditionally, so that overwrote a known make with
    the UNKNOWN placeholder and blanked the configured keywords."""
    await VehicleService.create_vehicle(
        vehicle_id=VID,
        year="2019",
        model="CX-5",
        make="MAZDA",
        trim="SUV",
        keywords=["battery"],
    )

    await VehicleService.create_vehicle(vehicle_id=VID, year="2019", model="CX-5")

    row = await _pg_row()
    assert row["make"] == "MAZDA"
    assert row["trim"] == "SUV"
    assert list(row["keywords"]) == ["battery"]


@pytest.mark.asyncio
async def test_the_api_reads_and_writes_one_store(clean):
    """What create returns, get returns, and list contains are the same row."""
    created = await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=["battery"]
    )
    fetched = await VehicleService.get_vehicle(VID)
    items, total = await VehicleService.list_vehicles(per_page=200)
    listed = next(v for v in items if v["vehicle_id"] == VID)

    assert created == fetched == listed
    assert total >= 1
    assert created["year"] == "2019", "year stays a string over the wire"
    assert created["id"].isdigit(), "id is the canonical row id"


@pytest.mark.asyncio
async def test_comm_count_is_derived_not_stored(clean):
    """The count follows the corpus. A stored copy is what let a card read 389
    above a detail page reading 0."""
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", keywords=[]
    )
    assert (await VehicleService.get_vehicle(VID))["comm_count"] == 0

    conn = await asyncpg.connect(get_settings().database_url)
    try:
        nhtsa_id = f"test-{VID}"
        comm_id = await conn.fetchval(
            "INSERT INTO communications (nhtsa_id) VALUES ($1) RETURNING id", nhtsa_id
        )
        row_id = await conn.fetchval("SELECT id FROM vehicles WHERE nhtsa_vehicle_id = $1", VID)
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            comm_id,
            row_id,
        )
        try:
            assert (await VehicleService.get_vehicle(VID))["comm_count"] == 1
        finally:
            await conn.execute("DELETE FROM communications WHERE nhtsa_id = $1", nhtsa_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_updating_keywords_leaves_the_make_alone(clean):
    """The router has always forwarded make and trim, but VehicleUpdate had no
    such fields, so every PATCH raised AttributeError before reaching here."""
    await VehicleService.create_vehicle(
        vehicle_id=VID, year="2019", model="CX-5", make="MAZDA", trim="SUV", keywords=[]
    )

    updated = await VehicleService.update_vehicle(VID, keywords=["brakes"])

    assert updated["keywords"] == ["brakes"]
    assert updated["make"] == "MAZDA"
    assert updated["trim"] == "SUV"


@pytest.mark.asyncio
async def test_updating_an_untracked_vehicle_is_a_miss(clean):
    assert await VehicleService.update_vehicle(VID, keywords=["brakes"]) is None


@pytest.mark.asyncio
async def test_a_non_numeric_year_is_rejected(clean):
    """year is a string over the wire and an integer column."""
    with pytest.raises(ValueError):
        await VehicleService.create_vehicle(vehicle_id=VID, year="twenty", model="CX-5")

    assert await _pg_row() is None
