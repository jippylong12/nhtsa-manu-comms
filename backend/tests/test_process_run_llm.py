"""`process.run_llm`: Batch API by default, Messages API with --sync, no vision pass (JIP-1572)."""

from types import SimpleNamespace

import pytest

from src.jobs import llm as llm_stage
from src.jobs import process


def _stats(**kw):
    base = dict(processed=3, failed=0, retried=0, tokens_in=10, tokens_out=5, cost_usd=0.01, errors=[])
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def seen(monkeypatch):
    calls: list[tuple] = []
    runs: list[tuple] = []

    async def run_sync(**kw):
        calls.append(("run_sync", kw))
        return _stats()

    async def submit_batch(**kw):
        calls.append(("submit_batch", kw))
        return ["batches/1"]

    async def collect_batch(names):
        calls.append(("collect_batch", names))
        return _stats()

    async def record_run(stage, summary, *a, **kw):
        runs.append((stage, summary))

    monkeypatch.setattr(llm_stage, "run_sync", run_sync)
    monkeypatch.setattr(llm_stage, "submit_batch", submit_batch)
    monkeypatch.setattr(llm_stage, "collect_batch", collect_batch)
    monkeypatch.setattr(process, "record_run", record_run)
    return calls, runs


@pytest.mark.asyncio
async def test_sync_runs_the_messages_api_once(seen):
    calls, runs = seen
    await process.run_llm(20, sync=True)
    assert calls == [("run_sync", {"limit": 20, "vehicle_id": None})]
    assert [r[1]["mode"] for r in runs] == ["sync"]


@pytest.mark.asyncio
async def test_batch_submits_and_collects_with_no_vision_pass(seen):
    calls, runs = seen
    await process.run_llm(None, sync=False)
    assert [c[0] for c in calls] == ["submit_batch", "collect_batch"]
    assert runs == [
        ("llm", {"processed": 3, "failed": 0, "mode": "batch", "jobs": ["batches/1"]})
    ]
