"""vehicle_catalog as the YMM -> vehicleId cache in front of NHTSA byYmmt.

Runs against the real Postgres table, cleaning up after itself, so the primary
key, the ON CONFLICT and the ordering are the real ones rather than a fake's idea
of them. Moved off Mongo in JIP-420; the properties being defended are the ones
JIP-410 established and are unchanged.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.communications import service as service_module
from src.communications.service import CommunicationService
from src.config import get_settings

pytestmark = pytest.mark.asyncio

# A year NHTSA will never publish and ids far outside its range, so these rows
# can never collide with catalog data the app actually serves.
TEST_YEAR = 1901
CIVIC_IDS = (999_100_001, 999_100_002)
SEEDED_ID = 999_100_003

# Two variants shaped exactly as NHTSAClient.get_vehicle_variants returns them.
CIVIC_VARIANTS = [
    {
        "vehicleId": CIVIC_IDS[0],
        "ncapId": 12345,
        "modelYear": TEST_YEAR,
        "make": "HONDA",
        "model": "CIVIC",
        "trim": "2 DR",
        "series": "FWD",
        "vehicleDescription": "CIVIC 2 DR FWD",
    },
    {
        "vehicleId": CIVIC_IDS[1],
        "ncapId": 12346,
        "modelYear": TEST_YEAR,
        "make": "HONDA",
        "model": "CIVIC",
        "trim": "4 DR",
        "series": "FWD",
        "vehicleDescription": "CIVIC 4 DR FWD",
    },
]


class FakeNHTSAClient:
    """Stands in for NHTSAClient and records every byYmmt call it receives."""

    calls: list[tuple] = []
    results: list[dict] = []

    async def get_vehicle_variants(self, year, make, model, trim=None):
        type(self).calls.append((year, make, model, trim))
        return list(type(self).results)


@pytest_asyncio.fixture
async def catalog(monkeypatch):
    """A clean slice of vehicle_catalog, wired into the service."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    conn = await asyncpg.connect(settings.database_url)

    async def wipe():
        await conn.execute("DELETE FROM vehicle_catalog WHERE year = $1", TEST_YEAR)

    await wipe()
    FakeNHTSAClient.calls = []
    FakeNHTSAClient.results = list(CIVIC_VARIANTS)
    monkeypatch.setattr(service_module, "NHTSAClient", FakeNHTSAClient)

    yield conn

    await wipe()
    await conn.close()


async def _rows(conn) -> list[dict]:
    return [
        dict(r)
        for r in await conn.fetch(
            "SELECT * FROM vehicle_catalog WHERE year = $1 ORDER BY vehicle_id", TEST_YEAR
        )
    ]


async def test_cache_miss_resolves_live_and_populates(catalog):
    variants = await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")

    assert [v["vehicleId"] for v in variants] == list(CIVIC_IDS)
    assert FakeNHTSAClient.calls == [(TEST_YEAR, "HONDA", "CIVIC", None)]

    rows = await _rows(catalog)
    assert [r["vehicle_id"] for r in rows] == list(CIVIC_IDS)
    assert rows[0]["year"] == TEST_YEAR
    assert rows[0]["make"] == "HONDA"
    assert rows[0]["model"] == "CIVIC"
    assert rows[0]["ncap_id"] == 12345
    assert rows[0]["cached_at"] is not None


async def test_second_request_is_served_from_cache(catalog):
    await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")
    assert len(FakeNHTSAClient.calls) == 1

    variants = await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")

    assert len(FakeNHTSAClient.calls) == 1, "cache hit must not call NHTSA again"
    assert [v["vehicleId"] for v in variants] == list(CIVIC_IDS)


async def test_cached_response_matches_the_live_response_shape(catalog):
    """The whole point of a cache: the caller cannot tell which one it got."""
    live = await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")
    cached = await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")

    assert cached == live


async def test_lookup_is_case_insensitive_on_make_and_model(catalog):
    await CommunicationService.get_discovery_variants(TEST_YEAR, "honda", "civic")
    assert len(FakeNHTSAClient.calls) == 1

    variants = await CommunicationService.get_discovery_variants(TEST_YEAR, "Honda", "Civic")

    assert len(FakeNHTSAClient.calls) == 1
    assert [v["vehicleId"] for v in variants] == list(CIVIC_IDS)


async def test_repeated_resolution_does_not_duplicate_rows(catalog):
    """vehicle_id is the primary key, so a re-resolve refreshes rather than adds."""
    await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")
    await catalog.execute("DELETE FROM vehicle_catalog WHERE year = $1", TEST_YEAR)
    await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")
    # Not cleared this time: the upsert has to be idempotent on its own.
    await CommunicationService._cache_variants(TEST_YEAR, "HONDA", "CIVIC", CIVIC_VARIANTS)

    assert len(await _rows(catalog)) == 2


async def test_seed_script_rows_serve_the_cache_read_path(catalog):
    """Warm-start rows carry no explicit cached_at; the column defaults."""
    await catalog.execute(
        """
        INSERT INTO vehicle_catalog (vehicle_id, ncap_id, year, make, model, trim, series)
        VALUES ($1, 21005, $2, 'FORD', 'F-150 SUPER CREW', 'PU/CC', '2WD')
        """,
        SEEDED_ID,
        TEST_YEAR,
    )

    variants = await CommunicationService.get_discovery_variants(
        TEST_YEAR, "FORD", "F-150 SUPER CREW"
    )

    assert FakeNHTSAClient.calls == [], "seeded rows are a cache hit"
    assert variants == [
        {
            "vehicleId": SEEDED_ID,
            "ncapId": 21005,
            "modelYear": TEST_YEAR,
            "make": "FORD",
            "model": "F-150 SUPER CREW",
            "trim": "PU/CC",
            "series": "2WD",
            "vehicleDescription": "F-150 SUPER CREW PU/CC 2WD",
        }
    ]


async def test_trim_filters_the_cached_variant_set(catalog):
    await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")

    variants = await CommunicationService.get_discovery_variants(
        TEST_YEAR, "HONDA", "CIVIC", trim="4 DR"
    )

    assert len(FakeNHTSAClient.calls) == 1
    assert [v["vehicleId"] for v in variants] == [CIVIC_IDS[1]]


async def test_trim_matching_is_case_insensitive_like_byymmt(catalog):
    """byYmmt resolves trim=`4 dr` to `4 DR`; the in-memory filter must agree."""
    variants = await CommunicationService.get_discovery_variants(
        TEST_YEAR, "HONDA", "CIVIC", trim="4 dr"
    )

    assert [v["vehicleId"] for v in variants] == [CIVIC_IDS[1]]


async def test_cache_write_failure_still_returns_resolved_variants(catalog, monkeypatch):
    """A cache write that fails must not fail a request whose variants have
    already resolved. The cache is an optimisation, not the answer."""
    real_get_pool = service_module.get_pool
    calls = {"n": 0}

    async def fail_on_the_write(*args, **kwargs):
        # The read acquires a pool first, the write second. Failing only the
        # second isolates the write path, which is what is under test.
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_get_pool(*args, **kwargs)
        raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")

    monkeypatch.setattr(service_module, "get_pool", fail_on_the_write)

    variants = await CommunicationService.get_discovery_variants(TEST_YEAR, "HONDA", "CIVIC")

    assert [v["vehicleId"] for v in variants] == list(CIVIC_IDS)
    assert calls["n"] == 2, "the write was attempted and swallowed its failure"

    monkeypatch.undo()
    assert await _rows(catalog) == [], "the failed write stored nothing"


async def test_empty_live_result_is_not_cached(catalog):
    """An unknown YMM must stay resolvable later, not be pinned as an empty hit.

    A cached emptiness reads identically to a cache miss, so storing one would
    make the answer permanent.
    """
    FakeNHTSAClient.results = []

    assert await CommunicationService.get_discovery_variants(TEST_YEAR, "ACME", "NOPE") == []
    assert await _rows(catalog) == []

    FakeNHTSAClient.results = list(CIVIC_VARIANTS)
    variants = await CommunicationService.get_discovery_variants(TEST_YEAR, "ACME", "NOPE")

    assert len(variants) == 2
    assert len(FakeNHTSAClient.calls) == 2


async def test_the_discovery_path_reaches_no_mongo():
    """The point of the milestone. Discovery was the last reader of the Mongo
    catalog, so with it gone only JIP-421's cleanup remains."""
    import inspect

    source = inspect.getsource(service_module)
    for forbidden in ("get_database", "motor", "pymongo"):
        assert forbidden not in source, f"{forbidden} is still reachable from discovery"
