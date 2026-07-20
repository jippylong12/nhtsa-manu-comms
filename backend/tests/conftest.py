"""Shared fixtures.

Tests run against the real Railway database inside a transaction that is always
rolled back, so they exercise the actual constraints, generated columns, and
`ON CONFLICT` behaviour rather than a mock's idea of them, while leaving no
residue behind.
"""

import asyncpg
import pytest_asyncio

from src.config import get_settings
from src.db import close_pool


@pytest_asyncio.fixture(autouse=True)
async def _reset_pool():
    """Dispose of the global asyncpg pool around every test.

    pytest-asyncio 1.x runs each test on its own event loop by default, but
    `src.db.get_pool()` memoises a pool bound to the loop that first created it.
    Reusing that pool on a later test's loop raises "Event loop is closed".
    Resetting here keeps the pool bound to the currently running loop.
    """
    await close_pool()
    yield
    await close_pool()


@pytest_asyncio.fixture
async def conn():
    """A connection whose transaction is rolled back after every test."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    connection = await asyncpg.connect(settings.database_url)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def vehicle(conn):
    """A tracked vehicle to hang communications off."""
    row = await conn.fetchrow(
        """
        INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model, keywords)
        VALUES (999001, 2026, 'CHEVROLET', 'TEST TRUCK', ARRAY['battery'])
        RETURNING id, nhtsa_vehicle_id, year, make, model, keywords
        """
    )
    return dict(row)
