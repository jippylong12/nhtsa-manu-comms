"""The extract stage works one tracked vehicle's backlog, selected by row id (JIP-1561)."""

import uuid

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs import extract
from src.jobs import process

TAG = f"EXSCOPE{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def two_vehicles():
    """Two tracked vehicles, each with one pending, unextracted document."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)

    async def wipe():
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{TAG}-%")
        await conn.execute("DELETE FROM vehicles WHERE model LIKE $1", f"{TAG}%")

    try:
        await wipe()
        ids = {}
        for name in ("A", "B"):
            vid = await conn.fetchval(
                "INSERT INTO vehicles (year, make, model) VALUES (2098, 'TESTMAKE', $1) "
                "RETURNING id",
                f"{TAG} {name}",
            )
            comm = await conn.fetchval(
                "INSERT INTO communications (nhtsa_id, status) VALUES ($1, 'pending') RETURNING id",
                f"{TAG}-{name}",
            )
            await conn.execute(
                "INSERT INTO comm_documents (communication_id, url) VALUES ($1, $2)",
                comm,
                f"https://example.test/{TAG}-{name}.pdf",
            )
            await conn.execute(
                "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
                comm,
                vid,
            )
            ids[name] = vid
        yield ids
    finally:
        await wipe()
        await conn.close()


@pytest.mark.asyncio
async def test_extract_only_selects_the_given_vehicles_documents(two_vehicles, monkeypatch):
    requested: list[str] = []

    async def fake_download(_client, url):
        requested.append(url)
        return None, "stubbed: no network in tests", False

    monkeypatch.setattr(extract, "download", fake_download)

    await process.run_extract(None, vehicle_id=two_vehicles["A"])

    assert requested == [f"https://example.test/{TAG}-A.pdf"]


@pytest.mark.asyncio
async def test_a_communication_without_documents_is_processed_as_summary_only(
    two_vehicles, monkeypatch
):
    """JIP-1577: no document published means the NHTSA summary is the content."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        comm = await conn.fetchval(
            "INSERT INTO communications (nhtsa_id, status, summary) "
            "VALUES ($1, 'pending', 'NHTSA summary only') RETURNING id",
            f"{TAG}-NODOC",
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            comm,
            two_vehicles["A"],
        )

        async def fake_download(_client, url):
            return None, "stubbed: no network in tests", False

        monkeypatch.setattr(extract, "download", fake_download)
        await process.run_extract(None, vehicle_id=two_vehicles["A"])

        row = await conn.fetchrow(
            "SELECT status, status_reason, processed_at FROM communications WHERE id = $1", comm
        )
    finally:
        await conn.close()

    assert row["status"] == "processed"
    assert row["status_reason"] == "summary only: NHTSA published no document"
    assert row["processed_at"] is not None
