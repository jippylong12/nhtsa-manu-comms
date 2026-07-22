"""A vehicle's communication count, and why it can no longer disagree (JIP-417).

This file replaces test_product_matching.py, and the reason it looks nothing
like it is the point.

The Mongo design stored one communication per (nhtsa_id, vehicle_id) with the
bulletin's own `associatedProducts` alongside, so counting a vehicle's bulletins
meant reconciling two different NHTSA spellings of the same vehicle:
/vehicles/models says "F-150 SUPER CREW" and a bulletin says "F-150". Commit
47a8611 fixed a matcher that required them to be identical, which made every
cab-style-qualified vehicle count zero. The escaped regexes and the `$elemMatch`
existed to make that reconciliation safe.

The corpus does not have that problem to solve. NHTSA already decides which
bulletins belong to a vehicleId, sync records each one as a
`communication_vehicles` row, and the count is those rows. No name is compared,
so no name can be compared wrongly. What is worth testing now is that the count
really is the link count, that a cab-style-qualified vehicle is not a special
case, and that the number the card shows is the number the detail page lists.
"""

import uuid

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.corpus import service as corpus_service
from src.vehicles.service import VehicleService

VID = 999_000_000 + int(uuid.uuid4().int % 100_000)

# The exact shape of the original bug: a vehicle whose name carries a cab style,
# and bulletins that name only the base model.
CAB_STYLE_MODEL = "F-150 SUPER CREW"


@pytest_asyncio.fixture
async def vehicle_with_corpus():
    """A cab-style-qualified vehicle with three linked communications."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    conn = await asyncpg.connect(settings.database_url)

    async def wipe():
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"count-{VID}-%")
        await conn.execute("DELETE FROM vehicles WHERE nhtsa_vehicle_id = $1", VID)

    try:
        await wipe()
        row_id = await conn.fetchval(
            """
            INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model)
            VALUES ($1, 2020, 'FORD', $2) RETURNING id
            """,
            VID,
            CAB_STYLE_MODEL,
        )
        for n in range(3):
            comm_id = await conn.fetchval(
                """
                INSERT INTO communications (nhtsa_id, communication_number, status, raw)
                VALUES ($1, $2, 'processed', $3::jsonb) RETURNING id
                """,
                f"count-{VID}-{n}",
                f"TSB-{n}",
                # The bulletin names the base model, not the cab-style name.
                '{"associatedProducts": [{"productModel": "F-150"}]}',
            )
            await conn.execute(
                "INSERT INTO communication_vehicles (communication_id, vehicle_id) "
                "VALUES ($1, $2)",
                comm_id,
                row_id,
            )
        yield row_id
    finally:
        await wipe()
        await conn.close()


@pytest.mark.asyncio
async def test_a_cab_style_qualified_vehicle_counts_its_bulletins(vehicle_with_corpus):
    """The original bug: 2020 F-150 SUPER CREW counted 0 of its bulletins.

    Nothing compares "F-150 SUPER CREW" to "F-150" any more, so the name cannot
    make the count wrong.
    """
    vehicle = await VehicleService.get_vehicle(VID)

    assert vehicle["model"] == CAB_STYLE_MODEL
    assert vehicle["comm_count"] == 3


@pytest.mark.asyncio
async def test_the_card_count_and_the_detail_list_cannot_disagree(vehicle_with_corpus):
    """The symptom that started this project: a card reading 389 above a detail
    page reading 0. Both numbers now come from the same rows."""
    vehicle = await VehicleService.get_vehicle(VID)
    _, listed_total = await corpus_service.list_communications(vehicle_id=VID, per_page=200)

    assert vehicle["comm_count"] == listed_total


@pytest.mark.asyncio
async def test_a_model_name_carrying_regex_metacharacters_is_harmless(vehicle_with_corpus):
    """ "CLA 350 4MATIC (EV)" and "RANGER [XLT" are real dropdown entries. They
    used to have to be escaped into a query; now they are never a pattern."""
    for hostile in ("CLA 350 4MATIC (EV)", "RANGER [XLT", "F-150 (2WD)"):
        updated = await VehicleService.update_vehicle(VID, model=hostile)
        assert updated["model"] == hostile.upper()
        assert updated["comm_count"] == 3, f"{hostile!r} changed the count"


@pytest.mark.asyncio
async def test_a_vehicle_with_no_corpus_counts_zero_rather_than_vanishing(
    vehicle_with_corpus,
):
    """comm_count is a correlated subquery, not a GROUP BY join, so a vehicle
    with nothing linked still returns a row."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    empty_vid = VID + 1
    try:
        await conn.execute(
            """
            INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model)
            VALUES ($1, 2020, 'FORD', 'RANGER')
            """,
            empty_vid,
        )
        vehicle = await VehicleService.get_vehicle(empty_vid)
        assert vehicle is not None
        assert vehicle["comm_count"] == 0
    finally:
        await conn.execute("DELETE FROM vehicles WHERE nhtsa_vehicle_id = $1", empty_vid)
        await conn.close()


@pytest.mark.asyncio
async def test_unlinking_a_communication_moves_the_count(vehicle_with_corpus):
    """Derived, not stored: there is no second copy to go stale."""
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute("DELETE FROM communications WHERE nhtsa_id = $1", f"count-{VID}-0")
    finally:
        await conn.close()

    assert (await VehicleService.get_vehicle(VID))["comm_count"] == 2
