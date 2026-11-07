"""The in-app refresh: preview, run bookkeeping, and the concurrency guard.

The stages themselves are covered by their own tests. What is new here is the
contract around them: an estimate that spends nothing, a run row that survives
the connection that started it, exactly one run at a time, and honest reporting
when part of the work did not finish.

Runs against the real database inside transactions that are rolled back.
"""

import asyncio
import uuid
from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs import repository as repo
from src.refresh import estimate, service

# Identity of the test vehicle, plus a row id no vehicle has.
IDENTITY = (2019, "MAZDA", "CX 5")
NO_SUCH_VEHICLE = 999_999_999
TAG = uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def tracked():
    """A tracked vehicle with no corpus, removed afterwards."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        conn = await asyncpg.connect(settings.database_url)
        try:
            # refresh_runs cascades from vehicles.
            await conn.execute(
                "DELETE FROM vehicles WHERE year = $1 AND make = $2 AND model = $3", *IDENTITY
            )
        finally:
            await conn.close()

    await wipe()
    conn = await asyncpg.connect(settings.database_url)
    try:
        # The active-run lock is global by design, so a real refresh running
        # against this database would make every test here fail on the unique
        # index. Say so plainly rather than producing a dozen confusing errors.
        holder = await conn.fetchval(
            "SELECT vehicle_id FROM refresh_runs WHERE active_lock IS NOT NULL"
        )
        if holder is not None:
            pytest.skip(f"a refresh is active (vehicle row {holder}); the lock is global")

        row_id = await conn.fetchval(
            """
            INSERT INTO vehicles (year, make, model)
            VALUES ($1, $2, $3) RETURNING id
            """,
            *IDENTITY,
        )
    finally:
        await conn.close()
    yield row_id
    await wipe()


async def _insert_run(vehicle_row_id: int, **overrides) -> int:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        return await conn.fetchval(
            """
            INSERT INTO refresh_runs (vehicle_id, status, active_lock)
            VALUES ($1, $2, $3) RETURNING id
            """,
            vehicle_row_id,
            overrides.get("status", "running"),
            overrides.get("active_lock", True),
        )
    finally:
        await conn.close()


# --------------------------------------------------------------------------
# The concurrency guard
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_one_run_can_be_active_at_a_time(tracked):
    """The partial unique index is the guard, not application logic, so two
    workers racing cannot both pass a check-then-insert."""
    await _insert_run(tracked)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_run(tracked)


@pytest.mark.asyncio
async def test_a_finished_run_releases_the_lock(tracked):
    """active_lock goes NULL when a run ends, and NULLs do not collide."""
    first = await _insert_run(tracked)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "UPDATE refresh_runs SET status='succeeded', active_lock=NULL, "
            "finished_at=now() WHERE id=$1",
            first,
        )
    finally:
        await conn.close()

    second = await _insert_run(tracked)
    assert second != first


@pytest.mark.asyncio
async def test_the_lock_flag_cannot_disagree_with_the_status(tracked):
    """A row claiming to be finished while still holding the lock would block
    every future refresh, so the constraint refuses it."""
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO refresh_runs (vehicle_id, status, active_lock) "
                "VALUES ($1, 'succeeded', true)",
                tracked,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_an_abandoned_run_is_reaped(tracked):
    """A worker killed mid-run leaves the lock held. Without reaping, one dead
    row would stop the feature working forever."""
    run_id = await _insert_run(tracked)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "UPDATE refresh_runs SET heartbeat_at = now() - $2::interval WHERE id = $1",
            run_id,
            service.STALE_AFTER + timedelta(minutes=1),
        )
    finally:
        await conn.close()

    await service._reap_abandoned()

    run = await service.latest_run(tracked)
    assert run["status"] == "failed"
    assert "abandoned" in run["error"]
    # And the lock is free again.
    assert await _insert_run(tracked)


@pytest.mark.asyncio
async def test_a_live_run_is_not_reaped(tracked):
    await _insert_run(tracked)
    await service._reap_abandoned()
    assert (await service.latest_run(tracked))["status"] == "running"


# --------------------------------------------------------------------------
# Progress and reporting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_leaves_unset_fields_alone(tracked):
    """A caller with only a new note should not have to restate the counters."""
    run_id = await _insert_run(tracked)
    await service._progress(run_id, stage="read", note="Reading documents", done=3, total=10)
    await service._progress(run_id, note="Still reading")

    run = await service.latest_run(tracked)
    assert run["stage"] == "read"
    assert run["stage_note"] == "Still reading"
    assert (run["done"], run["total"]) == (3, 10)


@pytest.mark.asyncio
async def test_progress_promotes_a_queued_run_to_running(tracked):
    run_id = await _insert_run(tracked, status="queued")
    await service._progress(run_id, stage="sync", note="Starting")
    assert (await service.latest_run(tracked))["status"] == "running"


@pytest.mark.asyncio
async def test_progress_beats_the_heartbeat(tracked):
    run_id = await _insert_run(tracked)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "UPDATE refresh_runs SET heartbeat_at = now() - interval '10 minutes' WHERE id=$1",
            run_id,
        )
        before = await conn.fetchval("SELECT heartbeat_at FROM refresh_runs WHERE id=$1", run_id)
        await service._progress(run_id, note="working")
        after = await conn.fetchval("SELECT heartbeat_at FROM refresh_runs WHERE id=$1", run_id)
    finally:
        await conn.close()
    assert after > before


@pytest.mark.asyncio
async def test_finishing_records_metered_spend_and_frees_the_lock(tracked):
    """Only one run is ever active, so everything metered since it started
    belongs to it."""
    run_id = await _insert_run(tracked)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "INSERT INTO usage_events (kind, model, cost_usd) VALUES ('llm_extract', 't', 0.25)"
        )
        await service._finish(run_id, "succeeded", counts={"analysed": 4})
        row = await conn.fetchrow(
            "SELECT status, active_lock, actual_cost_usd, counts, finished_at "
            "FROM refresh_runs WHERE id = $1",
            run_id,
        )
    finally:
        # The synthetic ledger row must not linger; it would skew the estimator.
        cleanup = await asyncpg.connect(get_settings().database_url)
        try:
            await cleanup.execute("DELETE FROM usage_events WHERE model = 't'")
        finally:
            await cleanup.close()
        await conn.close()

    assert row["status"] == "succeeded"
    assert row["active_lock"] is None
    assert row["finished_at"] is not None
    assert float(row["actual_cost_usd"]) >= 0.25


@pytest.mark.asyncio
async def test_a_deleted_vehicle_takes_its_runs_with_it(tracked):
    """refresh_runs is per-vehicle bookkeeping with no value of its own."""
    await _insert_run(tracked)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute("DELETE FROM vehicles WHERE id = $1", tracked)
        assert (
            await conn.fetchval("SELECT count(*) FROM refresh_runs WHERE vehicle_id = $1", tracked)
            == 0
        )
    finally:
        await conn.close()


# --------------------------------------------------------------------------
# The estimate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_per_document_comes_from_the_ledger():
    """Hardcoding it would go stale the moment the model or prompt changed."""
    per_doc = await estimate.cost_per_document()
    assert per_doc > 0
    # Sanity band around the measured $0.0005 to $0.001 range. A figure outside
    # this means the ledger query is counting the wrong rows, which is the
    # failure that would quietly misquote every user.
    assert 0.0001 < per_doc < 0.01


@pytest.mark.asyncio
async def test_documents_per_bulletin_never_under_quotes():
    """Under-quoting a price is worse than over-quoting it."""
    assert await estimate.documents_per_bulletin() >= 1.0


def test_zero_documents_costs_nothing_and_takes_no_time():
    """'Nothing to do' has to be a real answer, not a run that bills nothing
    and achieves nothing."""
    summary = estimate.summarise(new_bulletins=0, documents=0, per_document=0.0006)
    assert summary["estimated_cost_high_usd"] == 0
    assert summary["estimated_seconds"] == 0


def test_the_estimate_is_a_range_not_a_point():
    summary = estimate.summarise(new_bulletins=42, documents=42, per_document=0.0006)
    assert summary["estimated_cost_low_usd"] < summary["estimated_cost_high_usd"]


def test_duration_is_live_rate_with_no_queue_floor():
    """100 bulletins at 0.8 s plus 100 documents at 0.7 s is 150 s; no batch floor."""
    assert estimate.duration_range(0, 0).mid == 0
    assert estimate.duration_range(100, 100).mid == pytest.approx(150)


def test_small_amounts_are_described_without_false_precision():
    assert estimate.describe_cost(estimate.Range(0.0001, 0.0002)) == "under a cent"
    assert estimate.describe_cost(estimate.Range(0.02, 0.04)) == "about $0.03"


def test_durations_read_as_english():
    assert estimate.describe_duration(estimate.Range(20, 40)) == "under a minute"
    assert estimate.describe_duration(estimate.Range(200, 280)) == "around 4 minutes"
    assert "to" in estimate.describe_duration(estimate.Range(600, 1800))


# --------------------------------------------------------------------------
# Vehicle scoping, the thing that keeps the bill matching the estimate
# --------------------------------------------------------------------------


def test_vehicle_scope_passes_everything_through_when_unset():
    """The CLI jobs pass NULL and must keep processing every vehicle."""
    sql = repo.vehicle_scope("$1")
    assert "$1::bigint IS NULL" in sql


@pytest.mark.asyncio
async def test_the_stages_only_see_the_vehicle_they_were_given(tracked, conn):
    """A refresh quotes a price for one vehicle's backlog. If a stage drained
    another vehicle's too, the bill could not match the estimate."""
    other = await conn.fetchval(
        """
        INSERT INTO vehicles (year, make, model)
        VALUES (2019, 'MAZDA', 'CX 9') RETURNING id
        """
    )
    for row_id, nid in ((tracked, f"scope-mine-{TAG}"), (other, f"scope-other-{TAG}")):
        comm_id = await conn.fetchval(
            "INSERT INTO communications (nhtsa_id, status) VALUES ($1, 'pending') RETURNING id",
            nid,
        )
        await conn.execute(
            "INSERT INTO comm_documents (communication_id, url, extracted_text) "
            "VALUES ($1, $2, 'text')",
            comm_id,
            f"https://example.test/{nid}.pdf",
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            comm_id,
            row_id,
        )

    scoped = await conn.fetch(
        f"""
        SELECT d.id FROM comm_documents d
        JOIN communications c ON c.id = d.communication_id
        WHERE d.llm_summary IS NULL AND c.status = 'pending'
          AND {repo.vehicle_scope('$1')}
        """,
        tracked,
    )
    unscoped = await conn.fetch(
        f"""
        SELECT d.id FROM comm_documents d
        JOIN communications c ON c.id = d.communication_id
        WHERE d.llm_summary IS NULL AND c.status = 'pending'
          AND {repo.vehicle_scope('$1')}
        """,
        None,
    )

    assert len(scoped) == 1, "the scoped stage picked up another vehicle's work"
    assert len(unscoped) > len(scoped), "an unscoped stage still sees everything"


# --------------------------------------------------------------------------
# Nothing to do
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_of_an_unknown_vehicle_is_a_miss():
    assert await service.preview(NO_SUCH_VEHICLE) is None


@pytest.mark.asyncio
async def test_starting_for_an_untracked_vehicle_raises():
    with pytest.raises(LookupError):
        await service.start(NO_SUCH_VEHICLE)


@pytest.mark.asyncio
async def test_starting_returns_a_usable_run_and_spawns_the_worker(tracked, monkeypatch):
    """The happy path of start(), with the worker stubbed so nothing is billed.

    Worth its own test because everything downstream reads the returned row: an
    INSERT whose RETURNING was invisible to the same statement handed back None,
    which stranded the run holding the global lock with no worker behind it and
    took the whole feature down until the row was reaped half an hour later.
    """

    async def fake_preview(_vid):
        return {"documents_to_process": 7, "estimated_cost_high_usd": 0.0042}

    spawned: list[int] = []

    async def fake_execute(run_id, _vid):
        spawned.append(run_id)

    monkeypatch.setattr(service, "preview", fake_preview)
    monkeypatch.setattr(service, "_execute", fake_execute)

    run = await service.start(tracked)

    assert run is not None, "start() must return the row it just inserted"
    assert run["vehicle_id"] == tracked
    assert run["status"] == "queued"
    assert run["total"] == 7
    assert run["expected_documents"] == 7
    assert run["estimated_cost_usd"] == pytest.approx(0.0042)

    # Let the spawned task reach its first await.
    await asyncio.sleep(0)
    assert spawned == [run["id"]], "the background worker was never started"


@pytest.mark.asyncio
async def test_a_run_row_survives_the_api_response_model(tracked):
    """asyncpg returns jsonb as text, and the response model wants a dict. The
    mismatch turned every status poll into a 500 as soon as a run existed."""
    from src.refresh.schemas import RefreshRun

    run_id = await _insert_run(tracked)
    await service._progress(run_id, stage="read", note="Reading documents")

    run = await service.latest_run(tracked)
    assert isinstance(run["counts"], dict)
    # The real assertion: this is what the endpoint does, and it used to raise.
    assert RefreshRun(**run).counts == {}

    await service._finish(run_id, "succeeded", counts={"analysed": 2})
    finished = await service.latest_run(tracked)
    assert RefreshRun(**finished).counts == {"analysed": 2}


@pytest.mark.asyncio
async def test_a_second_start_conflicts_rather_than_double_spending(tracked, monkeypatch):
    """Two clicks on refresh must not run the pipeline twice."""

    async def fake_preview(_vid):
        return {"documents_to_process": 0, "estimated_cost_high_usd": 0.0}

    started: list[int] = []

    async def fake_execute(run_id, _vid):
        started.append(run_id)
        await asyncio.sleep(0)

    monkeypatch.setattr(service, "preview", fake_preview)
    monkeypatch.setattr(service, "_execute", fake_execute)

    await _insert_run(tracked)

    with pytest.raises(service.RefreshConflict) as caught:
        await service.start(tracked)

    assert caught.value.active["vehicle_id"] == tracked
    assert started == [], "the conflicting call must not have started any work"
