"""The system degrades honestly when NHTSA or static.nhtsa.gov is down (JIP-435).

No network: the flat-file fetch and NHTSAClient are patched at the boundary. D3
(every byNhtsaId fails) lives in test_sync_flatfile.py beside its fake client.
"""

import logging

import httpx
import pytest
import pytest_asyncio

from src.communications import flatfile
from src.config import get_settings
from src.refresh import service

import asyncpg

IDENTITY = (2099, "TESTDEG", "NOWHERE")


async def _true():
    return True


class _NeverConstructed:
    def __init__(self, *a, **kw):
        raise AssertionError("NHTSAClient must not be constructed here")


# --- D1: static.nhtsa.gov down at the head of a run ---------------------------------


def _all_windows_fail(calls=None):
    async def report(conn, *a, **kw):
        if calls is not None:
            calls.append(kw)
        return {
            "W-2024": {"status": "error", "error": "ConnectError: down", "scanned": []},
            "W-2025": {"status": "error", "error": "ConnectError: down", "scanned": []},
        }

    return report


def _stub_run(monkeypatch, progress, finished, synced, vehicle_id=1):
    async def record_progress(run_id, **kw):
        progress.append((kw.get("stage"), kw.get("note")))

    async def record_finish(run_id, status, **kw):
        finished.append((status, kw.get("error")))

    async def a_vehicle(conn):
        return [{"id": vehicle_id}]

    async def sync_called(conn, client, vehicle):
        synced.append(vehicle)
        raise RuntimeError("stop after sync")

    monkeypatch.setattr(service, "_progress", record_progress)
    monkeypatch.setattr(service, "_finish", record_finish)
    monkeypatch.setattr(service.repo, "tracked_vehicles", a_vehicle)
    monkeypatch.setattr(service, "NHTSAClient", lambda: object())
    monkeypatch.setattr(service.sync_job, "sync_vehicle", sync_called)


@pytest.mark.asyncio
async def test_a_never_scanned_vehicle_whose_scan_fails_fails_the_run_without_syncing(
    untracked_in_index, monkeypatch
):
    progress, finished, synced = [], [], []
    monkeypatch.setattr(flatfile, "refresh_index", _all_windows_fail())
    _stub_run(monkeypatch, progress, finished, synced)

    await service._execute(1, untracked_in_index)

    assert progress[0] == ("sync", "Scanning NHTSA's bulletin files for this vehicle")
    assert finished == [
        ("failed", "Could not read NHTSA's bulletin files. Try again in a few minutes.")
    ]
    assert synced == []


@pytest.mark.asyncio
async def test_a_previously_scanned_vehicle_continues_to_sync_and_the_warning_names_the_window(
    untracked_in_index, monkeypatch, caplog
):
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) VALUES ($1, 'W-OLD', 'e')",
            untracked_in_index,
        )
    finally:
        await conn.close()
    progress, finished, synced = [], [], []
    calls: list = []
    monkeypatch.setattr(flatfile, "refresh_index", _all_windows_fail(calls))
    _stub_run(monkeypatch, progress, finished, synced, untracked_in_index)

    with caplog.at_level(logging.WARNING):
        await service._execute(1, untracked_in_index)

    assert calls[0]["vehicles"] == [untracked_in_index]
    assert synced, "the run must reach sync on bounded staleness"
    assert any(
        r.levelno == logging.WARNING and "W-2024" in r.getMessage() for r in caplog.records
    )


async def _scan_every_ingested_window(conn, vehicle_id, *, skip=0):
    """A matching-etag scan row for every ingested window (all but `skip` of them)."""
    await conn.execute(
        "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) "
        "SELECT $1, source_window, etag FROM flatfile_ingests "
        "ORDER BY source_window OFFSET $2",
        vehicle_id,
        skip,
    )


# --- D2: preview with nothing in the index ------------------------------------------


@pytest_asyncio.fixture
async def untracked_in_index():
    """A committed vehicle whose scan listed nothing (no vehicle_bulletins rows)."""
    settings = get_settings()

    async def wipe(c):
        await c.execute(
            "DELETE FROM vehicles WHERE year=$1 AND make=$2 AND model=$3", *IDENTITY
        )

    c = await asyncpg.connect(settings.database_url)
    try:
        await wipe(c)
        vid = await c.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id", *IDENTITY
        )
        assert not await c.fetchval(
            "SELECT EXISTS (SELECT 1 FROM vehicle_bulletins WHERE vehicle_id=$1)", vid
        )
        yield vid
    finally:
        await wipe(c)
        await c.close()


@pytest.mark.asyncio
async def test_preview_of_a_never_scanned_vehicle_is_scan_pending_and_never_calls_nhtsa(
    untracked_in_index, monkeypatch
):
    monkeypatch.setattr(service, "NHTSAClient", _NeverConstructed)

    result = await service.preview(untracked_in_index)

    assert result["scan_pending"] is True
    assert result["listed_by_nhtsa"] == 0
    assert result["new_bulletins"] == 0
    assert result["up_to_date"] is False


@pytest.mark.asyncio
async def test_preview_of_a_scanned_vehicle_with_no_bulletins_is_up_to_date(
    untracked_in_index, monkeypatch
):
    monkeypatch.setattr(service, "NHTSAClient", _NeverConstructed)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await _scan_every_ingested_window(conn, untracked_in_index)
    finally:
        await conn.close()

    result = await service.preview(untracked_in_index)

    assert result["scan_pending"] is False
    assert result["up_to_date"] is True


@pytest.mark.asyncio
async def test_preview_of_a_vehicle_scanned_against_only_some_windows_is_still_scan_pending(
    untracked_in_index, monkeypatch
):
    monkeypatch.setattr(service, "NHTSAClient", _NeverConstructed)
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        windows = await conn.fetchval("SELECT count(*) FROM flatfile_ingests")
        if windows < 2:
            await conn.execute(
                "INSERT INTO flatfile_ingests (source_window, etag, row_count) "
                "VALUES ('TESTPEND-A', 'a', 1), ('TESTPEND-B', 'b', 1) "
                "ON CONFLICT DO NOTHING"
            )
        # Scanned at every ingested window but the last: its bulletins may be there.
        await conn.execute(
            "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) "
            "SELECT $1, source_window, etag FROM flatfile_ingests "
            "ORDER BY source_window LIMIT (SELECT count(*) - 1 FROM flatfile_ingests)",
            untracked_in_index,
        )
        try:
            result = await service.preview(untracked_in_index)
        finally:
            await conn.execute("DELETE FROM flatfile_ingests WHERE source_window LIKE 'TESTPEND-%'")
    finally:
        await conn.close()

    assert result["scan_pending"] is True
    assert result["listed_by_nhtsa"] == 0
    assert result["up_to_date"] is False


# --- D4: one window fails, another succeeds -----------------------------------------


@pytest.mark.asyncio
async def test_one_failing_window_does_not_stop_or_touch_the_others(conn, monkeypatch):
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) "
        "VALUES ('TESTD4-BAD', 'old-etag', 7), ('TESTD4-OK', 'same-etag', 3)"
    )
    # Every tracked vehicle is already scanned at the OK window's etag, so nothing is stale.
    await conn.execute(
        "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) "
        "SELECT id, 'TESTD4-OK', 'same-etag' FROM vehicles WHERE active"
    )

    async def head(client, window):
        if window == "TESTD4-BAD":
            raise httpx.ConnectError("window unreachable")
        return "same-etag", None

    monkeypatch.setattr(flatfile, "head_window", head)

    report = await flatfile.refresh_index(
        conn, client=object(), windows=["TESTD4-BAD", "TESTD4-OK"]
    )

    assert report["TESTD4-BAD"]["status"] == "error"
    assert report["TESTD4-OK"]["status"] == "unchanged"
    bad = await conn.fetchrow(
        "SELECT etag, row_count FROM flatfile_ingests WHERE source_window = 'TESTD4-BAD'"
    )
    assert (bad["etag"], bad["row_count"]) == ("old-etag", 7)


# --- The analyse stage waits on live calls (JIP-1572) -------------------------------


@pytest.mark.asyncio
async def test_the_analyse_stage_reads_documents_live_not_through_a_batch_queue(monkeypatch):
    from types import SimpleNamespace

    from src.jobs import extract as extract_stage
    from src.jobs import llm as llm_stage
    from src.jobs import process as processor
    from src.jobs import sync as sync_job
    from src.vehicles.service import VehicleService

    progress: list[tuple[str, str]] = []
    llm_calls: list[tuple] = []

    async def record_progress(run_id, **kw):
        progress.append((kw.get("stage"), kw.get("note")))

    async def noop(*a, **kw):
        return None

    async def a_vehicle(conn):
        return [{"id": 1}]

    async def synced(conn, client, vehicle):
        return SimpleNamespace(new_comms=40, new_docs=40, skipped_known=0, fetch_failures=0)

    async def extracted(limit, vehicle_id=None):
        return SimpleNamespace(extracted=40)

    async def record_llm(limit, sync, vehicle_id=None):
        llm_calls.append((limit, sync, vehicle_id))

    async def state(vehicle_id):
        return dict(analysed=40, still_working=0, gave_up=0, unavailable=0, total=40)

    async def pruned():
        return 0, 0

    monkeypatch.setattr(service, "_progress", record_progress)
    monkeypatch.setattr(service, "_finish", noop)
    monkeypatch.setattr(service, "_scan", lambda vehicle_id: _true())
    monkeypatch.setattr(service, "_vehicle_state", state)
    monkeypatch.setattr(service, "NHTSAClient", lambda: object())
    monkeypatch.setattr(service.repo, "tracked_vehicles", a_vehicle)
    monkeypatch.setattr(sync_job, "sync_vehicle", synced)
    monkeypatch.setattr(VehicleService, "touch_fetched_at", noop)
    monkeypatch.setattr(processor, "run_extract", extracted)
    monkeypatch.setattr(processor, "run_llm", record_llm)
    monkeypatch.setattr(processor, "run_embed", noop)
    monkeypatch.setattr(llm_stage, "promote_completed_communications", noop)
    monkeypatch.setattr(extract_stage, "prune_archive", pruned)

    await service._execute(1, 1)

    assert llm_calls == [(None, True, 1)]
    assert (
        "analyse",
        "Reading each document with the model. This is the long part",
    ) in progress
