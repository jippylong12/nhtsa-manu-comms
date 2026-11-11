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
    class _Calls(list):
        pass

    calls: list[tuple] = _Calls()
    runs: list[tuple] = []

    async def run_sync(**kw):
        calls.append(("run_sync", kw))
        return _stats()

    async def submit_batch(**kw):
        calls.append(("submit_batch", kw))
        return ["batches/1"]

    async def collect_batch(names, **kw):
        calls.append(("collect_batch", names))
        return _stats()

    async def record_run(stage, summary, *a, **kw):
        runs.append((stage, summary))

    monkeypatch.setattr(llm_stage, "run_sync", run_sync)
    monkeypatch.setattr(llm_stage, "submit_batch", submit_batch)
    monkeypatch.setattr(llm_stage, "collect_batch", collect_batch)
    # The batch ledger (llm_batches) is faked: `open_now` is what is still out.
    open_now: list[str] = []
    calls.open_now = open_now  # type: ignore[attr-defined]

    async def open_batches():
        return list(open_now)

    async def track_batches(ids):
        calls.append(("track", list(ids)))
        open_now.extend(ids)

    async def mark_batches_collected(ids):
        calls.append(("collected", list(ids)))
        for b in ids:
            if b in open_now:
                open_now.remove(b)

    monkeypatch.setattr(process, "record_run", record_run)
    monkeypatch.setattr(process, "open_batches", open_batches)
    monkeypatch.setattr(process, "track_batches", track_batches)
    monkeypatch.setattr(process, "mark_batches_collected", mark_batches_collected)
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
    assert [c[0] for c in calls] == ["submit_batch", "track", "collect_batch", "collected"]
    assert runs == [
        ("llm", {"processed": 3, "failed": 0, "mode": "batch", "jobs": ["batches/1"]})
    ]


@pytest.mark.asyncio
async def test_a_batch_still_running_is_never_sent_again_and_is_collected_before_anything_new(
    seen, monkeypatch
):
    calls, runs = seen
    still_running = {"batches/1"}

    waits: list = []

    async def collect_batch(names, **kw):
        calls.append(("collect_batch", names))
        waits.append(kw.get("max_wait"))
        return _stats(unfinished=[n for n in names if n in still_running])

    monkeypatch.setattr(llm_stage, "collect_batch", collect_batch)

    # Night 1: the batch outlives the wait. It stays on the books.
    await process.run_llm(None, sync=False)
    assert calls.open_now == ["batches/1"]

    # Night 2: still running at Anthropic. Its documents are still pending here, and
    # sending them again would pay for them twice, so nothing new is submitted.
    calls.clear()
    await process.run_llm(None, sync=False)
    assert [c[0] for c in calls] == ["collect_batch"]
    assert calls.open_now == ["batches/1"]

    # Night 3: it has ended. Its results are stored first, then new work is sent.
    still_running.clear()
    calls.clear()
    await process.run_llm(None, sync=False)
    assert calls[:2] == [("collect_batch", ["batches/1"]), ("collected", ["batches/1"])]
    assert calls[2][0] == "submit_batch"
    assert waits[1] == 0  # a batch from an earlier night is checked, never waited on


@pytest.mark.asyncio
async def test_the_batch_watch_stores_a_batch_that_ended_and_embeds_without_submitting(
    seen, monkeypatch
):
    calls, runs = seen
    calls.open_now.append("batches/late")
    embedded: list = []

    async def run_embed(limit, vehicle_id=None):
        embedded.append(limit)

    monkeypatch.setattr(process, "run_embed", run_embed)

    assert await process.collect_open_batches() == 3
    assert [c[0] for c in calls] == ["collect_batch", "collected"]  # no submit_batch
    assert calls.open_now == [] and embedded == [None]

    # Nothing out: a check does nothing at all.
    calls.clear()
    assert await process.collect_open_batches() == 0
    assert calls == []


@pytest.mark.asyncio
async def test_the_batch_watch_leaves_a_running_batch_alone_and_stands_aside_for_the_nightly(
    seen, monkeypatch
):
    from contextlib import asynccontextmanager

    calls, runs = seen
    calls.open_now.append("batches/slow")

    async def collect_batch(names, **kw):
        calls.append(("collect_batch", names))
        return _stats(processed=0, unfinished=list(names))

    monkeypatch.setattr(llm_stage, "collect_batch", collect_batch)
    assert await process.collect_open_batches() == 0
    assert calls.open_now == ["batches/slow"]  # still tracked for the next check

    @asynccontextmanager
    async def taken(wait):
        yield False

    monkeypatch.setattr(process, "batch_lock", taken)
    calls.clear()
    assert await process.collect_open_batches() == 0
    assert calls == []  # the nightly holds the slot: nothing is collected twice
