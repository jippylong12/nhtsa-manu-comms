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
import json
import os
import pathlib
from functools import lru_cache

import asyncpg
import pytest
import pytest_asyncio

from src.config import Settings, get_settings
from src.db import close_pool

_QUERY_EMBEDDINGS = pathlib.Path(__file__).parent / "fixtures" / "query_embeddings.json"


@lru_cache
def _recorded_embeddings() -> dict[str, list[float]]:
    """The replayed query vectors, parsed once rather than per test."""
    return json.loads(_QUERY_EMBEDDINGS.read_text()) if _QUERY_EMBEDDINGS.exists() else {}


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


@pytest.fixture(autouse=True)
def _replay_query_embeddings(monkeypatch):
    """Serve query embeddings from a recording instead of calling Gemini.

    `embed_query` is a real, billed HTTPS request. Two tests in
    `test_usage_ledger.py` reach it through `service.search()`, so every full
    run cost money and, worse, put an unbounded network call inside the suite:
    one run hung 40+ minutes blocked in a TLS read that could not time out.
    (`src/gemini.py` now bounds that too, but not making the call is better
    than failing it after two minutes.)

    Replayed rather than faked. The recording is the vector Gemini actually
    returned for that query, so retrieval, the cosine numbers, and the
    confidence gate in `compose()` behave exactly as they do in production —
    the tailgate query still scores 0.72 and still reaches the composer. A
    synthetic vector (like `DUMMY_EMBEDDING` in test_symptom_retrieval.py)
    would score near zero against real documents and silently turn the ledger
    tests into refusal tests that never bill a composition at all.

    Nothing about the ledger assertions is weakened: `search_embed` tokens come
    from `QueryCost.record_embed()`, which counts characters in the query
    string and never read the API response in the first place.

    Autouse so a new `embed_query` caller cannot quietly reintroduce billed
    spend; an unrecorded query fails loudly rather than reaching the network.
    To add one, see tools/record_query_embeddings.py.

    Scope is `embed_query` only, not a blanket network block. `embed_pending`
    (`embed_content`) and `compose._generate` (`generate_content`) still reach
    Gemini if a test calls them — no test calls the former, and the latter is
    deliberate in `test_a_symptom_search_records_its_spend`, the JIP-409
    acceptance test that real model spend lands in the ledger. Every such call
    is bounded by `GEMINI_TIMEOUT_SECONDS` (src/gemini.py).
    """

    async def _replay(text: str) -> list[float]:
        vector = _recorded_embeddings().get((text or "").strip())
        if vector is None:
            raise RuntimeError(
                f"No recorded query embedding for {text!r}. The suite does not call "
                f"the embeddings API. Add the query to tools/record_query_embeddings.py "
                f"and run: cd backend && python3 -m tools.record_query_embeddings"
            )
        return list(vector)

    # Patched at each import site, not just the definition: `service` and
    # `retrieval` bound the name at import, so patching `src.jobs.embed` alone
    # would leave both of them calling the real one.
    for module in ("src.jobs.embed", "src.symptom_search.service", "src.symptom_search.retrieval"):
        monkeypatch.setattr(f"{module}.embed_query", _replay)


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
        INSERT INTO vehicles (year, make, model, keywords)
        VALUES (2026, 'CHEVROLET', 'TEST TRUCK', ARRAY['battery'])
        RETURNING id, year, make, model, keywords
        """
    )
    return dict(row)


@pytest.fixture(autouse=True)
def _no_batch_ledger_writes(request, monkeypatch):
    """`process` tracks Batch API jobs in `llm_batches` through the pool, outside any test
    transaction. Tests that drive the pipeline with fake batches must not leave rows behind,
    so the three ledger functions are in-memory here. `tests/test_llm_batches.py` opts out
    and exercises the real SQL."""
    if request.node.get_closest_marker("real_batch_ledger"):
        return
    from src.jobs import process

    out: list[str] = []

    async def open_batches():
        return list(out)

    async def track_batches(ids):
        out.extend(ids)

    async def mark_batches_collected(ids):
        for b in ids:
            if b in out:
                out.remove(b)

    monkeypatch.setattr(process, "open_batches", open_batches)
    monkeypatch.setattr(process, "track_batches", track_batches)
    monkeypatch.setattr(process, "mark_batches_collected", mark_batches_collected)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_batch_ledger: use the real llm_batches table instead of the fake"
    )
