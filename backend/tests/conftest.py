"""Shared fixtures.

Tests run against a real Postgres inside a transaction that is always rolled
back, so they exercise the actual constraints, generated columns, and
`ON CONFLICT` behaviour rather than a mock's idea of them, while leaving no
residue behind.

*Which* Postgres is `TEST_DATABASE_URL` when set, falling back to
`DATABASE_URL`. Point it at a local instance. Every test opens its own
connection (see `_reset_pool` below), and the suite is now ~200 tests, which is
more connection churn than Railway's public TCP proxy will absorb: past roughly
half the suite it starts refusing, and tests fail at fixture setup with
`TimeoutError` out of `asyncpg.connect()` rather than on any assertion.
Postgres itself was never the limit — `max_connections` is 100 and a failing
run showed 9 in use. Locally there is no proxy to throttle and no round trip to
pay, so the same suite is both reliable and roughly two orders of magnitude
faster. Setup, including matching Railway's exact Postgres and pgvector
versions: docs/database.md.
"""

import asyncio
import os

import asyncpg
import pytest
import pytest_asyncio

from src.config import Settings, get_settings
from src.db import close_pool


def _redirect_to_test_database() -> None:
    """Point every `get_settings()` consumer at `TEST_DATABASE_URL`.

    Rewriting the environment and clearing the cache, rather than teaching the
    `conn` fixture alone about the test URL, is what makes this total:
    `get_pool()`, `src.refresh.estimate`, and the service layer each read
    `database_url` for themselves, so a narrower seam would leave some tests on
    the remote database and bring the churn back with them.

    Runs at import, which conftest guarantees is before any test module's
    fixtures resolve.
    """
    url = Settings().test_database_url
    if not url:
        return
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()

    # Apply any migrations the local database has not seen. Without this, a
    # newly added migration turns into a scatter of unrelated-looking failures
    # in whichever tests happen to touch the new column.
    from src.db.migrate import migrate

    if asyncio.run(migrate()) != 0:
        raise RuntimeError("Test database migrations failed; see output above.")


_redirect_to_test_database()


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
