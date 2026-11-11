"""The LLM stage on Claude Haiku: live and Batch paths over the input plan.

The Anthropic client, the ledger, the database writes and the PDF planner are faked;
nothing here calls the real API.
"""

import base64
import json
import uuid
from types import SimpleNamespace

import anthropic
import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.config import get_settings

from src.jobs import llm, pdf_input

GOOD = {
    "summary": "Radio may show a black screen.",
    "symptoms": ["black screen"],
    "systems": ["infotainment"],
    "components": ["radio"],
    "remedy": "Reprogram the radio.",
    "applicability": "2024 Silverado EV",
    "doc_kind": "service_procedure",
    "applicability_years": [2024],
    "applicability_models": ["Silverado EV"],
    "applicability_conditions": "",
    "audience": "dealer",
}


def _msg(text, tin=1000, tout=100, stop="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tin, output_tokens=tout),
        stop_reason=stop,
    )


def _good(**over):
    return _msg(json.dumps({**GOOD, **over}))


class FakeAnthropic:
    """Scripted ``messages.create`` responses (an Exception is raised) and batch endpoints."""

    def __init__(self, responses=(), results=(), status="ended"):
        self.calls = []
        self._responses = list(responses)
        self.created = []
        self._results = list(results)
        self._status = status
        self.messages = SimpleNamespace(
            create=self._create,
            batches=SimpleNamespace(
                create=self._batch_create, retrieve=self._retrieve, results=self._results_for
            ),
        )

    def _create(self, **params):
        self.calls.append(params)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _batch_create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def _retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self._status)

    def _results_for(self, batch_id):
        return iter(self._results)


def _succeeded(doc_id, text, tin=800, tout=90):
    return SimpleNamespace(
        custom_id=str(doc_id),
        result=SimpleNamespace(type="succeeded", message=_msg(text, tin, tout)),
    )


def _errored(doc_id, kind="errored"):
    return SimpleNamespace(custom_id=str(doc_id), result=SimpleNamespace(type=kind, error=None))


@pytest.fixture
def stage(monkeypatch):
    """Fakes for everything around the Anthropic client; returns the recorded side effects."""
    rec = SimpleNamespace(
        usage=[],
        stored=[],
        failed=[],
        docs=[],
        plans={},
        client=None,
        archived=[],
    )

    async def record_usage(kind, model, **kw):
        rec.usage.append({"kind": kind, "model": model, **kw})

    async def pending(limit=None, vehicle_id=None):
        return list(rec.docs)

    async def store(doc, clean, tin, tout, batch):
        rec.stored.append((doc["id"], clean, tin, tout, batch))

    async def mark_failed(comm_id, reason, retryable=True):
        rec.failed.append((comm_id, reason, retryable))

    async def promote():
        return 0

    async def ensure_archived(url):
        rec.archived.append(url)
        return url

    def plan(path, page_count, method, has_text):
        return rec.plans[path]

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(llm, "record_usage", record_usage)
    async def batch_documents(batch_id):
        return list(rec.docs)

    monkeypatch.setattr(llm, "_pending_documents", pending)
    monkeypatch.setattr(llm, "_batch_documents", batch_documents)
    monkeypatch.setattr(llm, "_store", store)
    monkeypatch.setattr(llm, "_mark_failed", mark_failed)
    monkeypatch.setattr(llm, "promote_completed_communications", promote)
    monkeypatch.setattr(llm, "_sleep", no_sleep)
    monkeypatch.setattr(pdf_input, "ensure_archived", ensure_archived)
    monkeypatch.setattr(pdf_input, "plan", plan)
    monkeypatch.setattr(llm, "_client", lambda: rec.client)

    def add_doc(doc_id, kind, payloads=(), text="extracted words"):
        url = f"https://x/{doc_id}.pdf"
        rec.docs.append(
            {
                "id": doc_id,
                "url": url,
                "extracted_text": text,
                "extraction_method": "pymupdf",
                "page_count": 4,
                "communication_id": doc_id * 10,
            }
        )
        rec.plans[url] = pdf_input.InputPlan(kind, tuple(payloads), "test")

    rec.add_doc = add_doc
    return rec


async def test_a_pdf_plan_sends_the_pdf_as_one_document_block_and_meters_it(stage):
    stage.add_doc(1, "pdf", [b"%PDF-bytes"])
    stage.client = FakeAnthropic([_good()])

    stats = await llm.run_sync()

    assert len(stage.client.calls) == 1
    content = stage.client.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert content[0]["source"]["data"] == base64.b64encode(b"%PDF-bytes").decode()
    assert content[1] == {"type": "text", "text": llm.PDF_PROMPT}
    assert stage.stored[0][0] == 1 and stage.stored[0][1]["summary"] == GOOD["summary"]
    assert stats.processed == 1
    assert len(stage.usage) == 1
    row = stage.usage[0]
    assert row["kind"] == "llm_extract" and row["model"] == "claude-haiku-5-5"
    assert row["batch"] is False and row["document_id"] == 1 and row["communication_id"] == 10
    assert row["meta"] == {"path": "live", "input": "pdf", "project": "schema-v2"}


async def test_a_pruned_archive_is_fetched_through_ensure_archived_before_the_call(stage):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_good()])

    await llm.run_sync()

    assert stage.archived == ["https://x/1.pdf"]


async def test_a_text_plan_sends_the_prompt_with_the_extracted_text(stage):
    stage.add_doc(2, "text", text="the bulletin words")
    stage.client = FakeAnthropic([_good()])

    await llm.run_sync()

    content = stage.client.calls[0]["messages"][0]["content"]
    assert content == llm.TEXT_PROMPT.format(text="the bulletin words")
    assert stage.usage[0]["meta"]["input"] == "text"


async def test_a_chunks_plan_makes_one_call_per_chunk_then_a_merge_call(stage):
    stage.add_doc(3, "chunks", [b"part-one", b"part-two"])
    stage.client = FakeAnthropic(
        [_good(summary="first part"), _good(summary="second part"), _good(summary="merged")]
    )

    await llm.run_sync()

    calls = stage.client.calls
    assert len(calls) == 3
    assert calls[0]["messages"][0]["content"][0]["type"] == "document"
    merge = calls[2]["messages"][0]["content"]
    assert isinstance(merge, str) and merge.startswith("Below are 2 partial extractions")
    assert "first part" in merge and "second part" in merge
    assert len(stage.stored) == 1 and stage.stored[0][1]["summary"] == "merged"
    assert [u["meta"]["input"] for u in stage.usage] == ["chunks"] * 3


async def test_a_malformed_response_is_retried_once_with_identical_parameters(stage):
    stage.add_doc(4, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_msg("not json"), _good()])

    stats = await llm.run_sync()

    assert stats.retried == 1 and stats.processed == 1
    assert stage.client.calls[0] == stage.client.calls[1]
    assert len(stage.usage) == 2, "the malformed response was billed too"
    assert stage.failed == []


async def test_two_malformed_responses_mark_the_communication_failed(stage):
    stage.add_doc(5, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_msg("not json"), _msg("still not json")])

    stats = await llm.run_sync()

    assert stats.failed == 1 and stats.processed == 0
    assert len(stage.usage) == 2
    assert stage.failed[0][0] == 50 and stage.failed[0][1].startswith("LLM schema violation")
    assert stage.stored == []


async def test_a_response_cut_off_at_max_tokens_counts_as_a_validation_failure(stage):
    stage.add_doc(6, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_msg(json.dumps(GOOD), stop="max_tokens"), _good()])

    stats = await llm.run_sync()

    assert stats.retried == 1 and stats.processed == 1


def _msg_with_thinking(text):
    """A high-effort response: a thinking block comes before the text block."""
    msg = _msg(text)
    msg.content.insert(0, SimpleNamespace(type="thinking", thinking="let me read it"))
    return msg


async def test_a_thinking_block_before_the_text_block_is_ignored_on_the_live_path(stage):
    stage.add_doc(7, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_msg_with_thinking(json.dumps(GOOD))])

    stats = await llm.run_sync()

    assert stats.processed == 1 and stats.retried == 0
    assert stage.stored[0][1]["summary"] == GOOD["summary"]


async def test_a_thinking_block_before_the_text_block_is_ignored_on_the_batch_path(stage):
    stage.add_doc(8, "pdf", [b"%PDF"])
    result = _succeeded(8, json.dumps(GOOD))
    result.result.message = _msg_with_thinking(json.dumps(GOOD))
    stage.client = FakeAnthropic(results=[result])

    stats = await llm.collect_batch(["msgbatch_1"], poll_seconds=0)

    assert stats.processed == 1 and stats.failed == 0


async def test_an_invalid_key_stops_the_run_and_marks_nothing_failed(stage):
    stage.add_doc(7, "pdf", [b"%PDF"])
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
    stage.client = FakeAnthropic([anthropic.AuthenticationError("bad key", response=response, body=None)])

    with pytest.raises(llm.ExtractionFatal):
        await llm.run_sync()

    assert stage.failed == [] and stage.stored == []


async def test_a_rate_limit_is_retried_with_doubling_backoff(stage, monkeypatch):
    stage.add_doc(8, "pdf", [b"%PDF"])
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com"))
    limited = anthropic.RateLimitError("slow down", response=response, body=None)
    stage.client = FakeAnthropic([limited, limited, _good()])
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(llm, "_sleep", record_sleep)

    stats = await llm.run_sync()

    assert stats.processed == 1 and sleeps == [2.0, 4.0]


async def test_submit_batch_sends_pdf_and_text_documents_and_runs_chunks_live(stage):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "text", text="words")
    stage.add_doc(3, "chunks", [b"a", b"b"])
    stage.client = FakeAnthropic([_good(), _good(), _good(summary="merged")])

    ids = await llm.submit_batch()

    assert ids == ["msgbatch_1"]
    requests = stage.client.created[0]
    assert sorted(r["custom_id"] for r in requests) == ["1", "2"]
    by_id = {r["custom_id"]: r["params"] for r in requests}
    assert by_id["1"]["messages"][0]["content"][0]["type"] == "document"
    assert by_id["2"]["messages"][0]["content"] == llm.TEXT_PROMPT.format(text="words")
    assert len(stage.client.calls) == 3, "the chunks document ran live: two chunks and a merge"
    assert [s[0] for s in stage.stored] == [3]
    assert {u["meta"]["path"] for u in stage.usage} == {"live"}


async def test_submit_batch_splits_requests_over_the_byte_limit(stage, monkeypatch):
    stage.add_doc(1, "pdf", [b"x" * 100])
    stage.add_doc(2, "pdf", [b"y" * 100])
    stage.client = FakeAnthropic()
    monkeypatch.setattr(llm, "BATCH_MAX_BYTES", 1500)

    ids = await llm.submit_batch()

    assert len(ids) == 2 and [len(c) for c in stage.client.created] == [1, 1]


async def test_a_stray_error_storing_one_document_costs_that_document_not_the_run(
    stage, monkeypatch
):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic([_good(), _good()])
    stored = []

    async def store(doc, clean, tin, tout, batch):
        if doc["id"] == 1:
            raise asyncpg.exceptions.CharacterNotInRepertoireError("invalid byte sequence 0x00")
        stored.append(doc["id"])

    monkeypatch.setattr(llm, "_store", store)

    stats = await llm.run_sync(concurrency=1)

    assert stored == [2], "the sibling document was still stored"
    assert stats.processed == 1 and stats.failed == 1
    assert [c for c, _, _ in stage.failed] == [10]
    assert stage.failed[0][1].startswith("LLM stage error")


async def test_submit_batch_skips_a_document_the_planner_cannot_read(stage, monkeypatch):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "text", text="words")
    stage.client = FakeAnthropic()

    def plan(path, page_count, method, has_text):
        if path.endswith("/1.pdf"):
            raise ValueError("unsupported pdf structure")
        return stage.plans[path]

    monkeypatch.setattr(pdf_input, "plan", plan)

    ids = await llm.submit_batch()

    assert ids == ["msgbatch_1"]
    assert [r["custom_id"] for r in stage.client.created[0]] == ["2"]
    assert [c for c, _, _ in stage.failed] == [10]


async def test_a_fatal_error_on_a_chunked_document_submits_no_batch(stage):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "chunks", [b"a", b"b"])
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
    stage.client = FakeAnthropic(
        [anthropic.AuthenticationError("bad key", response=response, body=None)]
    )

    with pytest.raises(llm.ExtractionFatal):
        await llm.submit_batch()

    assert stage.client.created == [], "no batch whose id the caller would never see"
    assert stage.failed == []


async def test_collect_batch_stores_succeeded_and_fails_errored_results_and_meters_both(stage):
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "pdf", [b"%PDF"])
    stage.add_doc(3, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic(
        results=[
            _succeeded(2, json.dumps(GOOD)),
            _errored(1, "expired"),
            _succeeded(3, "not json", tin=700, tout=30),
            _succeeded(99, json.dumps(GOOD)),
        ]
    )

    stats = await llm.collect_batch(["msgbatch_1"], poll_seconds=0)

    assert [s[0] for s in stage.stored] == [2] and stage.stored[0][4] is True
    reasons = {c: r for c, r, _ in stage.failed}
    assert reasons[10].startswith("batch expired")
    assert reasons[30].startswith("LLM schema violation")
    assert stats.processed == 1 and stats.failed == 2
    assert any("could not be matched" in e for e in stats.errors)
    assert sorted((u["document_id"], u["batch"]) for u in stage.usage) == [(2, True), (3, True)]
    assert all(u["meta"] == {"path": "batch", "input": "pdf", "project": "schema-v2"} for u in stage.usage)


# --- promotion: severity from the classifier for catch-all types -------------------------

NS_PROMOTE = f"TESTPROMOTE-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def promotable():
    """Insert pending communications whose one document is done; clean up afterwards."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:

        async def add(suffix, comm_type, doc_kind):
            comm_id = await conn.fetchval(
                """
                INSERT INTO communications (nhtsa_id, communication_type, communication_date, status)
                VALUES ($1, $2, '2099-01-01', 'pending') RETURNING id
                """,
                f"{NS_PROMOTE}-{suffix}",
                comm_type,
            )
            await conn.execute(
                """
                INSERT INTO comm_documents (communication_id, url, llm_summary, doc_kind, audience)
                VALUES ($1, $2, 'done', $3, 'owner')
                """,
                comm_id,
                f"https://x/{NS_PROMOTE}-{suffix}.pdf",
                doc_kind,
            )
            return comm_id

        async def state(comm_id):
            return dict(
                await conn.fetchrow(
                    "SELECT status, severity, severity_source FROM communications WHERE id=$1",
                    comm_id,
                )
            )

        yield add, state
    finally:
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS_PROMOTE}%")
        await conn.close()


@pytest.mark.asyncio
async def test_promoting_a_catch_all_recall_is_classified_serious_and_a_tsb_keeps_its_type(
    promotable,
):
    add, state = promotable
    mc = await add("mc", "MC", "recall")
    tsb = await add("tsb", "TSB", "service_procedure")

    await llm.promote_completed_communications()

    mc_state = await state(mc)
    assert mc_state["status"] == "processed"
    assert mc_state["severity"] == "serious"
    assert mc_state["severity_source"] == "classifier"
    tsb_state = await state(tsb)
    assert tsb_state["status"] == "processed"
    assert tsb_state["severity_source"] == "type"


async def test_collect_batch_logs_progress_at_most_every_ten_minutes_while_waiting(
    stage, monkeypatch, caplog
):
    import logging

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    stage.client = FakeAnthropic(results=[])
    polls = {"n": 0}

    def retrieve(batch_id):
        polls["n"] += 1  # the first call is the look before the loop; one poll = 60 s after it
        status = "ended" if polls["n"] >= 26 else "in_progress"
        return SimpleNamespace(
            processing_status=status, request_counts=SimpleNamespace(processing=5, succeeded=0)
        )

    stage.client.messages.batches.retrieve = retrieve
    caplog.set_level(logging.INFO, logger="llm")

    await llm.collect_batch(["msgbatch_1"], poll_seconds=60, max_wait=3600)

    lines = [r.getMessage() for r in caplog.records if "waiting for batch" in r.getMessage()]
    assert len(lines) == 2, lines  # 25 minutes of polling: lines at 10 and 20 minutes
    assert "msgbatch_1" in lines[0] and "in_progress" in lines[0] and "10 min" in lines[0]


async def test_collect_batch_reports_a_batch_it_gave_up_waiting_for(stage, monkeypatch):
    """A batch still running at max_wait used to be dropped with only a warning, so the caller
    exited 0 and could resubmit the same documents; now the id comes back as unfinished."""

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    stage.client = FakeAnthropic(results=[])
    stage.client.messages.batches.retrieve = lambda batch_id: SimpleNamespace(
        processing_status="in_progress", request_counts=None
    )

    stats = await llm.collect_batch(["msgbatch_slow"], poll_seconds=60, max_wait=120)

    assert stats.unfinished == ["msgbatch_slow"]
    assert stats.errors == ["batch msgbatch_slow not ended after 120s"]
    assert stats.processed == 0 and stats.failed == 0


# --- review fixes: a document is paid for once, and one bad batch blocks nothing ----------


async def test_submit_records_which_documents_went_into_which_batch(stage, monkeypatch):
    recorded = []

    async def record_batch(batch_id, document_ids):
        recorded.append((batch_id, document_ids))

    monkeypatch.setattr(llm, "_record_batch", record_batch)
    stage.add_doc(1, "pdf", [b"%PDF"])
    stage.add_doc(2, "text")
    stage.client = FakeAnthropic()

    assert await llm.submit_batch() == ["msgbatch_1"]
    assert recorded == [("msgbatch_1", [1, 2])]


async def test_a_batch_whose_results_are_already_stored_is_not_metered_or_stored_again(stage):
    stage.add_doc(2, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic(results=[_succeeded(2, json.dumps(GOOD))])

    first = await llm.collect_batch(["msgbatch_1"], poll_seconds=0)
    second = await llm.collect_batch(["msgbatch_1"], poll_seconds=0)  # the watch, a moment later

    assert first.processed == 1 and second.processed == 0
    assert len(stage.usage) == 1 and len(stage.stored) == 1


class _Gone(anthropic.NotFoundError):
    def __init__(self):
        Exception.__init__(self, "batch not found")


async def test_a_batch_anthropic_no_longer_has_is_given_up_and_the_next_one_is_still_collected(
    stage, monkeypatch
):
    closed = []

    async def close_batch(batch_id):
        closed.append(batch_id)

    monkeypatch.setattr(llm, "_close_batch", close_batch)
    stage.add_doc(2, "pdf", [b"%PDF"])
    stage.client = FakeAnthropic(results=[_succeeded(2, json.dumps(GOOD))])
    retrieve = stage.client.messages.batches.retrieve

    def flaky(batch_id):
        if batch_id == "gone":
            raise _Gone()
        if batch_id == "down":
            raise RuntimeError("503")
        return retrieve(batch_id)

    stage.client.messages.batches.retrieve = flaky

    stats = await llm.collect_batch(["gone", "down", "msgbatch_1"], poll_seconds=0)

    assert "gone" in closed  # never collectable: closed, so it blocks no later run
    assert stats.unfinished == ["down"]  # could not be checked now: kept for the next check
    assert stats.processed == 1  # the good batch was still stored
