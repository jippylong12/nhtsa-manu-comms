"""GET /api/public/lookup: severity counts by revision family, availability, example vehicle.

Seeds a namespaced fixture on a committed connection (the endpoint opens its own
pool connections), drives the real app over ASGI, and removes its rows afterwards.
"""

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.config import get_settings
from src.main import create_app
from src.public import service

pytestmark = pytest.mark.asyncio

NS = "TESTPUB"
MAKE = "TESTPUBFORD"
# Larger than any real vehicle on the replica (the biggest has ~700 communications),
# so this vehicle wins "largest folded count" for the example.
BIG_FAMILIES = 900


async def _comm(conn, vid, nhtsa_id, number, date, severity, status="processed"):
    cid = await conn.fetchval(
        """
        INSERT INTO communications
            (nhtsa_id, communication_number, communication_type, communication_date,
             summary, status, severity)
        VALUES ($1, $2, 'TSB', $3::text::date, 'seed', $4, $5) RETURNING id
        """,
        nhtsa_id,
        number,
        date,
        status,
        severity,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid,
        vid,
    )


@pytest_asyncio.fixture
async def seeded():
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _cleanup(conn)
        v = await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES (2031, $1, 'F 150') RETURNING id",
            MAKE,
        )
        pending = await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES (2031, $1, 'RANGER') RETURNING id",
            MAKE,
        )
        big = await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES (2031, $1, 'BIG') RETURNING id",
            MAKE,
        )
        # serious: one single, one family of three revisions (latest 2099-03-15)
        await _comm(conn, v, f"{NS}-S1", "TPA100", "2099-01-10", "serious")
        for i, (num, d) in enumerate(
            (("TPB6202", "2099-01-01"), ("TPB6202A", "2099-02-01"), ("TPB6202C", "2099-03-15"))
        ):
            await _comm(conn, v, f"{NS}-S2-{i}", num, d, "serious")
        for i in range(3):
            await _comm(conn, v, f"{NS}-M{i}", f"TPM{i}00", "2099-02-02", "medium")
        await _comm(conn, v, f"{NS}-L1", "TPL100", "2099-01-05", "small")
        # a pending communication never counts
        await _comm(conn, v, f"{NS}-P0", "TPP100", "2099-12-31", "serious", status="pending")
        await _comm(conn, pending, f"{NS}-P1", "TPQ100", "2099-01-01", "serious", status="pending")
        for i in range(BIG_FAMILIES):
            await _comm(conn, big, f"{NS}-B{i}", f"TPZ{i:04d}", "2098-01-01", "small")
        yield {"v": v, "pending": pending, "big": big}
    finally:
        await _cleanup(conn)
        await conn.close()


async def _cleanup(conn):
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS}-%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


async def _get(**params):
    service.reset_example_cache()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get("/api/public/lookup", params=params)


async def test_counts_revision_families_by_severity(seeded):
    r = await _get(year=2031, make=MAKE, model="F 150")
    assert r.status_code == 200
    body = r.json()
    assert body["vehicleId"] == seeded["v"]
    assert body["availability"] == "ready"
    assert body["counts"] == {"serious": 2, "medium": 3, "small": 1}
    assert body["latestDate"].startswith("2099-03-15")
    assert body["example"] is None


async def test_tracked_vehicle_with_only_pending_is_processing(seeded):
    body = (await _get(year=2031, make=MAKE, model="RANGER")).json()
    assert body["vehicleId"] == seeded["pending"]
    assert body["availability"] == "processing"
    assert body["counts"] == {"serious": 0, "medium": 0, "small": 0}
    assert body["latestDate"] is None
    assert body["example"] is None


async def test_unknown_model_is_unknown_with_example(seeded):
    r = await _get(year=2031, make=MAKE, model="NOSUCH")
    assert r.status_code == 200
    body = r.json()
    assert body["vehicleId"] is None
    assert body["availability"] == "unknown"
    assert body["counts"] == {"serious": 0, "medium": 0, "small": 0}
    assert body["example"]["vehicleId"] == seeded["big"]
    assert body["example"]["counts"]["small"] == BIG_FAMILIES


async def test_make_case_and_model_spelling_are_normalised(seeded):
    a = (await _get(year=2031, make=MAKE, model="F 150")).json()
    b = (await _get(year=2031, make=MAKE.lower(), model="f-150")).json()
    assert b == a
    assert b["availability"] == "ready"


async def test_non_integer_year_is_400():
    r = await _get(year="abc", make="FORD", model="F 150")
    assert r.status_code == 400
