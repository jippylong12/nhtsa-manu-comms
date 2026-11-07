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


class _NeverConstructed:
    def __init__(self, *a, **kw):
        raise AssertionError("NHTSAClient must not be constructed here")


# --- D1: static.nhtsa.gov down at the head of a run ---------------------------------


@pytest.mark.asyncio
async def test_a_failed_index_refresh_logs_a_warning_and_returns(monkeypatch, caplog):
    async def down(conn, *a, **kw):
        raise httpx.ConnectError("static.nhtsa.gov unreachable")

    monkeypatch.setattr(flatfile, "refresh_index", down)

    with caplog.at_level(logging.WARNING):
        await service._freshen_index()  # must not raise

    assert any(
        r.levelno == logging.WARNING and "ConnectError" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_a_run_continues_to_the_sync_stage_when_the_index_is_unreachable(monkeypatch):
    async def down(conn, *a, **kw):
        raise httpx.ConnectError("static.nhtsa.gov unreachable")

    progress: list[tuple[str, str]] = []
    finished: list[tuple] = []

    async def record_progress(run_id, **kw):
        progress.append((kw.get("stage"), kw.get("note")))

    async def record_finish(run_id, status, **kw):
        finished.append((status, kw.get("error")))

    async def no_vehicles(conn):
        return []

    monkeypatch.setattr(flatfile, "refresh_index", down)
    monkeypatch.setattr(service, "_progress", record_progress)
    monkeypatch.setattr(service, "_finish", record_finish)
    monkeypatch.setattr(service.repo, "tracked_vehicles", no_vehicles)
    monkeypatch.setattr(service, "NHTSAClient", _NeverConstructed)

    await service._execute(1, 1)

    assert progress[-1] == ("sync", "Checking NHTSA for new bulletins")
    assert finished == [("failed", "the vehicle is no longer tracked")]


# --- D2: preview with nothing in the index ------------------------------------------


@pytest_asyncio.fixture
async def untracked_in_index():
    """A committed vehicle whose identity has no comm_products rows."""
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
            "SELECT EXISTS (SELECT 1 FROM comm_products WHERE make=$1 AND model_year=$2)",
            IDENTITY[1],
            IDENTITY[0],
        )
        yield vid
    finally:
        await wipe(c)
        await c.close()


@pytest.mark.asyncio
async def test_preview_with_an_empty_index_slice_is_up_to_date_and_never_calls_nhtsa(
    untracked_in_index, monkeypatch
):
    monkeypatch.setattr(service, "NHTSAClient", _NeverConstructed)

    result = await service.preview(untracked_in_index)

    assert result["listed_by_nhtsa"] == 0
    assert result["new_bulletins"] == 0
    assert result["up_to_date"] is True


# --- D4: one window fails, another succeeds -----------------------------------------


@pytest.mark.asyncio
async def test_one_failing_window_does_not_stop_or_touch_the_others(conn, monkeypatch):
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) "
        "VALUES ('TESTD4-BAD', 'old-etag', 7), ('TESTD4-OK', 'same-etag', 3)"
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
    monkeypatch.setattr(service, "_freshen_index", noop)
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
