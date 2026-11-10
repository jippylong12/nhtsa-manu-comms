"""The nightly run's daily spend cap: the LLM stage reads only what the cap still allows.

The stages and the ledger reads are faked at the module boundary; the budget arithmetic
and the pipeline's use of it are what is under test.
"""

import argparse
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.alerts import service as alerts_service
from src.jobs import run_pipeline

NOW = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)


class _Conn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def acquire(self):
        return _Conn()


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(calls=[], spent=0.0, per_doc=0.001, cap=10.0, since=None)

    async def get_pool():
        return _Pool()

    async def spent_since(conn, started_at):
        w.since = started_at
        return w.spent

    async def per_document(conn, sync):
        return w.per_doc

    async def run_extract(_):
        w.calls.append("extract")

    async def run_llm(limit, sync=False):
        w.calls.append(("llm", limit))

    async def run_embed(_):
        w.calls.append("embed")

    async def status():
        return {}

    async def run_alerts(send, generated_at, **kw):
        return alerts_service.AlertRunSummary()

    monkeypatch.setattr(run_pipeline, "get_pool", get_pool)
    monkeypatch.setattr(run_pipeline, "get_settings", lambda: SimpleNamespace(nightly_spend_cap_usd=w.cap))
    monkeypatch.setattr(run_pipeline.processor, "_spent_since", spent_since)
    monkeypatch.setattr(run_pipeline.processor, "_per_document_estimate", per_document)
    monkeypatch.setattr(run_pipeline.processor, "run_extract", run_extract)
    monkeypatch.setattr(run_pipeline.processor, "run_llm", run_llm)
    monkeypatch.setattr(run_pipeline.processor, "run_embed", run_embed)
    monkeypatch.setattr(run_pipeline.processor, "status", status)
    monkeypatch.setattr(alerts_service, "run_alerts", run_alerts)
    return w


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        skip_sync=True, skip_scan=True, sync_only=False, keep_pdfs=True,
        send_digest=False, sync_llm=False,
    )


@pytest.mark.asyncio
async def test_the_llm_stage_reads_only_what_the_cap_still_allows(world):
    world.spent, world.per_doc = 4.0, 0.002  # $6 left at $0.002 a document
    code = await run_pipeline._run(_args(), NOW)
    assert code == 0
    assert world.calls == ["extract", ("llm", 3000), "embed"]
    assert (NOW - world.since).total_seconds() == 24 * 3600


@pytest.mark.asyncio
async def test_a_reached_cap_skips_the_llm_stage_without_failing_the_run(world):
    world.spent = 10.0
    code = await run_pipeline._run(_args(), NOW)
    assert code == 0
    assert world.calls == ["extract", "embed"]


@pytest.mark.asyncio
async def test_a_cap_of_zero_means_no_cap(world):
    world.cap, world.spent = 0.0, 500.0
    await run_pipeline._run(_args(), NOW)
    assert ("llm", None) in world.calls


@pytest.mark.asyncio
async def test_no_cost_history_does_not_block_the_run(world):
    world.per_doc = 0.0
    await run_pipeline._run(_args(), NOW)
    assert ("llm", None) in world.calls
