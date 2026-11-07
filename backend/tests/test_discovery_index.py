"""Discovery is served from the flat-file index, and only from it (ADR 0001, M3).

The three picker dropdowns are now bounded Postgres queries against
`comm_products` -- no NHTSA call, so nothing in the interactive path can time out.
These tests seed a small, isolated slice of the index (a far-future year no real
corpus row uses, tagged with a private source_window) and assert the service
returns plain nameplates from it: years exclude the 9999 unknown sentinel, makes
are curated against TOP_MAKES, and models come back as the canonical nameplates.

Seeded through a committed connection and cleaned up after, because the service
reads through its own pool connection and would not see an uncommitted row.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.communications.service import CommunicationService
from src.config import get_settings

_TAG = "TESTDISC"
_YEAR = 2099  # newer than any real corpus row, so it sorts to the top deterministically


@pytest_asyncio.fixture
async def seeded():
    """Populate an isolated slice of comm_products, removed before and after."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    rows = [
        # (nhtsa_id, make, model, model_year)
        ("800001", "FORD", "F 150", _YEAR),
        ("800002", "FORD", "F 150 LIGHTNING BEV", _YEAR),
        ("800003", "FORD", "RANGER", _YEAR),
        ("800004", "HONDA", "CIVIC", _YEAR),
        ("800005", "ACMEZZ", "WIDGET", _YEAR),  # a make outside TOP_MAKES: must be curated out
        ("800006", "FORD", "BRONCO", _YEAR - 1),  # an older year, for the years query
        ("800007", "FORD", "UNKNOWN", 9999),  # the unknown-year sentinel: must be excluded
    ]

    async def wipe(conn):
        await conn.execute("DELETE FROM comm_products WHERE source_window = $1", _TAG)

    conn = await asyncpg.connect(settings.database_url)
    try:
        await wipe(conn)
        await conn.executemany(
            "INSERT INTO comm_products (nhtsa_id, make, model, model_year, summary, source_window) "
            "VALUES ($1, $2, $3, $4, 'x', $5)",
            [(nid, mk, md, yr, _TAG) for nid, mk, md, yr in rows],
        )
    finally:
        await conn.close()

    yield

    conn = await asyncpg.connect(settings.database_url)
    try:
        await wipe(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_years_exclude_the_unknown_sentinel_and_sort_newest_first(seeded):
    years = await CommunicationService.get_discovery_years()
    assert _YEAR in years, "the seeded model year is offered"
    assert _YEAR - 1 in years
    assert 9999 not in years, "9999 (unknown) must never be offered as a year"
    assert years.index(_YEAR) < years.index(_YEAR - 1), "years are newest first"


@pytest.mark.asyncio
async def test_makes_are_curated_against_top_makes(seeded):
    makes = await CommunicationService.get_discovery_makes(_YEAR)
    assert makes == ["FORD", "HONDA"], "only TOP_MAKES with comms that year, in TOP_MAKES order"
    assert "ACMEZZ" not in makes, "a make outside the curated list is not offered"


@pytest.mark.asyncio
async def test_models_are_plain_nameplates_sorted(seeded):
    models = await CommunicationService.get_discovery_models(_YEAR, "FORD")
    assert models == ["F 150", "F 150 LIGHTNING BEV", "RANGER"]


@pytest.mark.asyncio
async def test_model_lookup_is_case_insensitive_on_make(seeded):
    """The picker sends whatever case the make came in as; the index is uppercase."""
    assert await CommunicationService.get_discovery_models(_YEAR, "ford") == [
        "F 150",
        "F 150 LIGHTNING BEV",
        "RANGER",
    ]


@pytest.mark.asyncio
async def test_a_make_with_no_comms_that_year_returns_no_models(seeded):
    assert await CommunicationService.get_discovery_models(_YEAR, "TESLA") == []
