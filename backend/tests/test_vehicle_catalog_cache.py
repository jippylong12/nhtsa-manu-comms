"""vehicle_catalog as the YMM -> vehicleId cache in front of NHTSA byYmmt.

Runs against a real local MongoDB in a throwaway database that is dropped after
the module, so the upsert, the unique index, and the find/sort ordering are the
real ones rather than a fake's idea of them.
"""

import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from src.communications import service as service_module
from src.communications.service import CommunicationService
from src.config import get_settings

pytestmark = pytest.mark.asyncio

# Two real 2020 Civic variants, exactly as NHTSAClient.get_vehicle_variants
# shapes them.
CIVIC_VARIANTS = [
    {
        "vehicleId": 11393,
        "ncapId": 12345,
        "modelYear": 2020,
        "make": "HONDA",
        "model": "CIVIC",
        "trim": "2 DR",
        "series": "FWD",
        "vehicleDescription": "CIVIC 2 DR FWD",
    },
    {
        "vehicleId": 11868,
        "ncapId": 12346,
        "modelYear": 2020,
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
    """A clean vehicle_catalog in a throwaway database, wired into the service."""
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url, serverSelectionTimeoutMS=3000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("MongoDB not reachable at " + settings.mongodb_url)

    db_name = f"nhtsa_comms_test_{uuid.uuid4().hex[:12]}"
    db = client[db_name]
    await db.vehicle_catalog.create_index("vehicle_id", unique=True)

    monkeypatch.setattr(service_module, "get_database", lambda: db)
    FakeNHTSAClient.calls = []
    FakeNHTSAClient.results = list(CIVIC_VARIANTS)
    monkeypatch.setattr(service_module, "NHTSAClient", FakeNHTSAClient)

    yield db

    await client.drop_database(db_name)
    client.close()


async def test_cache_miss_resolves_live_and_populates(catalog):
    variants = await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    assert [v["vehicleId"] for v in variants] == [11393, 11868]
    assert FakeNHTSAClient.calls == [(2020, "HONDA", "CIVIC", None)]

    rows = await catalog.vehicle_catalog.find().sort("vehicle_id", 1).to_list(None)
    assert [r["vehicle_id"] for r in rows] == [11393, 11868]
    assert rows[0]["year"] == 2020
    assert rows[0]["make"] == "HONDA"
    assert rows[0]["model"] == "CIVIC"
    assert rows[0]["ncap_id"] == 12345


async def test_second_request_is_served_from_cache(catalog):
    await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")
    assert len(FakeNHTSAClient.calls) == 1

    variants = await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    assert len(FakeNHTSAClient.calls) == 1, "cache hit must not call NHTSA again"
    assert [v["vehicleId"] for v in variants] == [11393, 11868]


async def test_cached_response_matches_the_live_response_shape(catalog):
    live = await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")
    cached = await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    assert cached == live


async def test_lookup_is_case_insensitive_on_make_and_model(catalog):
    await CommunicationService.get_discovery_variants(2020, "honda", "civic")
    assert len(FakeNHTSAClient.calls) == 1

    variants = await CommunicationService.get_discovery_variants(2020, "Honda", "Civic")

    assert len(FakeNHTSAClient.calls) == 1
    assert [v["vehicleId"] for v in variants] == [11393, 11868]


async def test_repeated_resolution_does_not_duplicate_rows(catalog):
    await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")
    await catalog.vehicle_catalog.delete_many({})  # force a second live resolve
    await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")
    await catalog.vehicle_catalog.delete_many({})
    await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    assert await catalog.vehicle_catalog.count_documents({}) == 2


async def test_seed_script_rows_serve_the_cache_read_path(catalog):
    """Warm-start rows written by seed_vehicle_catalog.py carry no cached_at."""
    await catalog.vehicle_catalog.insert_one(
        {
            "vehicle_id": 11179,
            "ncap_id": 21005,
            "year": 2020,
            "make": "FORD",
            "model": "F-150 SUPER CREW",
            "trim": "PU/CC",
            "series": "2WD",
        }
    )

    variants = await CommunicationService.get_discovery_variants(2020, "FORD", "F-150 SUPER CREW")

    assert FakeNHTSAClient.calls == [], "seeded rows are a cache hit"
    assert variants == [
        {
            "vehicleId": 11179,
            "ncapId": 21005,
            "modelYear": 2020,
            "make": "FORD",
            "model": "F-150 SUPER CREW",
            "trim": "PU/CC",
            "series": "2WD",
            "vehicleDescription": "F-150 SUPER CREW PU/CC 2WD",
        }
    ]


async def test_trim_filters_the_cached_variant_set(catalog):
    await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    variants = await CommunicationService.get_discovery_variants(
        2020, "HONDA", "CIVIC", trim="4 DR"
    )

    assert len(FakeNHTSAClient.calls) == 1
    assert [v["vehicleId"] for v in variants] == [11868]


async def test_trim_matching_is_case_insensitive_like_byymmt(catalog):
    """byYmmt resolves trim=`4 dr` to `4 DR`; the in-memory filter must agree."""
    variants = await CommunicationService.get_discovery_variants(
        2020, "HONDA", "CIVIC", trim="4 dr"
    )

    assert [v["vehicleId"] for v in variants] == [11868]


async def test_cache_write_failure_still_returns_resolved_variants(catalog, monkeypatch):
    """A racing upsert must not fail a request whose variants already resolved."""

    async def boom(*args, **kwargs):
        raise DuplicateKeyError("E11000 duplicate key error: vehicle_id")

    monkeypatch.setattr(type(catalog.vehicle_catalog), "bulk_write", boom)

    variants = await CommunicationService.get_discovery_variants(2020, "HONDA", "CIVIC")

    assert [v["vehicleId"] for v in variants] == [11393, 11868]
    assert await catalog.vehicle_catalog.count_documents({}) == 0


async def test_empty_live_result_is_not_cached(catalog):
    """An unknown YMM must stay resolvable later, not be pinned as an empty hit."""
    FakeNHTSAClient.results = []

    assert await CommunicationService.get_discovery_variants(2020, "ACME", "NOPE") == []
    assert await catalog.vehicle_catalog.count_documents({}) == 0

    FakeNHTSAClient.results = list(CIVIC_VARIANTS)
    variants = await CommunicationService.get_discovery_variants(2020, "ACME", "NOPE")

    assert len(variants) == 2
    assert len(FakeNHTSAClient.calls) == 2
