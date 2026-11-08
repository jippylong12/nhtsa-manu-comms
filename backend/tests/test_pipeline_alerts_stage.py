"""The nightly pipeline's alerts stage (JIP-1594): runs after process, honours the send flag.

Every stage is faked at the module boundary; the pipeline's own ordering, flags and
fail-soft handling are what is under test.
"""

import argparse
from datetime import datetime, timezone

import pytest

from src.alerts import service as alerts_service
from src.jobs import run_pipeline


@pytest.fixture
def calls(monkeypatch):
    seen: list = []

    async def run_extract(_):
        seen.append("extract")

    async def run_llm(_, sync=False):
        seen.append("llm")

    async def run_embed(_):
        seen.append("embed")

    async def status():
        return {}

    async def prune():
        return 0, 0

    async def run_alerts(send, generated_at, **kw):
        seen.append(("alerts", send))

    monkeypatch.setattr(run_pipeline.processor, "run_extract", run_extract)
    monkeypatch.setattr(run_pipeline.processor, "run_llm", run_llm)
    monkeypatch.setattr(run_pipeline.processor, "run_embed", run_embed)
    monkeypatch.setattr(run_pipeline.processor, "status", status)
    monkeypatch.setattr(run_pipeline.extract_stage, "prune_archive", prune)
    monkeypatch.setattr(alerts_service, "run_alerts", run_alerts)
    return seen


def _args(**kw) -> argparse.Namespace:
    base = dict(
        skip_sync=True, skip_scan=True, sync_only=False, keep_pdfs=True,
        send_digest=False, sync_llm=False,
    )
    return argparse.Namespace(**{**base, **kw})


NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_alerts_run_after_process_as_a_dry_run_by_default(calls):
    code = await run_pipeline._run(_args(), NOW)
    assert code == 0
    assert calls == ["extract", "llm", "embed", ("alerts", False)]


@pytest.mark.asyncio
async def test_send_digest_sends_the_alerts(calls):
    await run_pipeline._run(_args(send_digest=True), NOW)
    assert calls[-1] == ("alerts", True)


@pytest.mark.asyncio
async def test_a_failing_alerts_stage_fails_the_run_without_crashing(calls, monkeypatch):
    async def boom(send, generated_at, **kw):
        raise RuntimeError("resend down")

    monkeypatch.setattr(alerts_service, "run_alerts", boom)
    assert await run_pipeline._run(_args(), NOW) == 1


def test_send_alerts_is_an_alias_of_send_digest():
    parser = run_pipeline._parser()
    assert parser.parse_args(["--send-alerts"]).send_digest is True
    assert parser.parse_args(["--send-digest"]).send_digest is True
    assert parser.parse_args([]).send_digest is False
