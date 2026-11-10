"""The ingest queue: cars added by users are read one at a time by a background worker.

The read itself (`refresh.service.run_now`) and the spend ledger are faked; the queue
table is real, on the test database, under a namespaced make.
"""

import os
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from src.refresh import queue as ingest_queue
from src.refresh import service as refresh_service

MAKE = "ZZQUEUE"


@pytest_asyncio.fixture
async def world(monkeypatch):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)
    w = SimpleNamespace(conn=conn, runs=[], budgets=[], outcome={}, spent=0.0, cap=10.0, per_doc=0.5)

    async def run_now(vehicle_id: int, llm_budget=None):
        w.runs.append(vehicle_id)
        w.budgets.append(llm_budget)
        result = w.outcome.get(vehicle_id, "succeeded")
        if isinstance(result, list):
            result = result.pop(0) if len(result) > 1 else result[0]
        if isinstance(result, Exception):
            raise result
        return {"status": result, "error": None if result == "succeeded" else "boom"}

    async def spent_since(c, started_at):
        return w.spent

    async def per_document(c, sync):
        assert sync is True  # the worker reads live, not through Batch
        return w.per_doc

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(refresh_service, "run_now", run_now)
    monkeypatch.setattr(ingest_queue.processor, "_spent_since", spent_since)
    monkeypatch.setattr(ingest_queue.processor, "_per_document_estimate", per_document)
    monkeypatch.setattr(
        ingest_queue, "get_settings", lambda: SimpleNamespace(nightly_spend_cap_usd=w.cap)
    )
    monkeypatch.setattr(ingest_queue.asyncio, "sleep", no_sleep)

    async def vehicle(model: str) -> int:
        return await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES (2031, $1, $2) RETURNING id", MAKE, model
        )

    async def queued() -> list[int]:
        rows = await conn.fetch(
            "SELECT q.vehicle_id FROM ingest_queue q JOIN vehicles v ON v.id = q.vehicle_id "
            "WHERE v.make = $1 ORDER BY q.requested_at, q.vehicle_id",
            MAKE,
        )
        return [r["vehicle_id"] for r in rows]

    w.vehicle, w.queued = vehicle, queued
    try:
        yield w
    finally:
        await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)
        await conn.close()


async def _only_ours(w, monkeypatch):
    """Scope the worker to this test's rows: the shared test database may hold others."""
    real = ingest_queue._next_vehicle

    async def scoped():
        return await w.conn.fetchval(
            "SELECT q.vehicle_id FROM ingest_queue q JOIN vehicles v ON v.id = q.vehicle_id "
            "WHERE v.make = $1 AND q.attempts < $2 ORDER BY q.requested_at, q.vehicle_id LIMIT 1",
            MAKE,
            ingest_queue.MAX_ATTEMPTS,
        )

    monkeypatch.setattr(ingest_queue, "_next_vehicle", scoped)
    return real


@pytest.mark.asyncio
async def test_queued_cars_are_read_oldest_first_and_leave_the_queue(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a, b = await world.vehicle("A"), await world.vehicle("B")
    await ingest_queue.enqueue(a)
    await ingest_queue.enqueue(b)
    assert await ingest_queue.drain() == 2
    assert world.runs == [a, b]
    assert await world.queued() == []


@pytest.mark.asyncio
async def test_queuing_the_same_car_twice_reads_it_once(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a = await world.vehicle("A")
    await ingest_queue.enqueue(a)
    await ingest_queue.enqueue(a)
    await ingest_queue.drain()
    assert world.runs == [a]


@pytest.mark.asyncio
async def test_a_failing_read_is_retried_a_few_times_then_left_for_the_nightly(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    bad, good = await world.vehicle("BAD"), await world.vehicle("GOOD")
    world.outcome[bad] = "failed"
    await ingest_queue.enqueue(bad)
    await ingest_queue.enqueue(good)
    assert await ingest_queue.drain() == 1
    assert world.runs.count(bad) == ingest_queue.MAX_ATTEMPTS
    assert good in world.runs
    row = await world.conn.fetchrow(
        "SELECT attempts, last_error FROM ingest_queue WHERE vehicle_id = $1", bad
    )
    assert row["attempts"] == ingest_queue.MAX_ATTEMPTS and row["last_error"] == "boom"


@pytest.mark.asyncio
async def test_a_crashing_read_does_not_stop_the_cars_behind_it(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    bad, good = await world.vehicle("BAD"), await world.vehicle("GOOD")
    world.outcome[bad] = RuntimeError("nhtsa down")
    await ingest_queue.enqueue(bad)
    await ingest_queue.enqueue(good)
    assert await ingest_queue.drain() == 1
    assert await world.queued() == [bad]


@pytest.mark.asyncio
async def test_at_the_daily_cap_nothing_is_read_and_the_queue_is_kept(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a = await world.vehicle("A")
    await ingest_queue.enqueue(a)
    world.spent = 10.0
    assert await ingest_queue.drain() == 0
    assert world.runs == []
    assert await world.queued() == [a]


@pytest.mark.asyncio
async def test_a_cap_of_zero_turns_the_cap_off(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a = await world.vehicle("A")
    await ingest_queue.enqueue(a)
    world.cap, world.spent = 0.0, 999.0
    assert await ingest_queue.drain() == 1


@pytest.mark.asyncio
async def test_a_busy_run_slot_is_waited_out_and_the_same_car_is_read(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a = await world.vehicle("A")
    world.outcome[a] = [refresh_service.RefreshConflict({}), "succeeded"]
    await ingest_queue.enqueue(a)
    assert await ingest_queue.drain() == 1
    assert world.runs == [a, a]
    assert await world.queued() == []


@pytest.mark.asyncio
async def test_a_vehicle_that_is_no_longer_tracked_is_dropped(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    a = await world.vehicle("A")
    world.outcome[a] = LookupError("not tracked")
    await ingest_queue.enqueue(a)
    assert await ingest_queue.drain() == 0
    assert await world.queued() == []


@pytest.mark.asyncio
async def test_a_read_is_given_only_the_documents_the_cap_still_allows(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    world.spent, world.cap, world.per_doc = 9.0, 10.0, 0.5  # $1 left: two documents
    a = await world.vehicle("BIG")
    await ingest_queue.enqueue(a)
    assert await ingest_queue.drain() == 1
    assert world.budgets == [2]


@pytest.mark.asyncio
async def test_without_a_cap_a_read_has_no_document_budget(world, monkeypatch):
    await _only_ours(world, monkeypatch)
    world.cap = 0
    a = await world.vehicle("FREE")
    await ingest_queue.enqueue(a)
    assert await ingest_queue.drain() == 1
    assert world.budgets == [None]


def test_the_smaller_of_the_local_limit_and_the_cap_budget_wins():
    assert refresh_service._llm_limit(None, None) is None
    assert refresh_service._llm_limit(25, None) == 25
    assert refresh_service._llm_limit(None, 2) == 2
    assert refresh_service._llm_limit(25, 2) == 2
    assert refresh_service._llm_limit(3, 40) == 3
