"""The extraction bake-off harness (JIP-1567).

Every test here is offline. The provider SDK clients and the HTTP layer are
replaced with fakes, and an autouse guard fails the test if the real thing is
ever constructed, so no test can spend money or open a connection.
"""

import asyncio
import base64
import copy
import json
import random
from collections import Counter
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from google import genai

from src.eval import extraction_bakeoff as eb
from src.jobs import llm

# --------------------------------------------------------------------------- guards


@pytest.fixture(autouse=True)
def _no_real_clients(monkeypatch):
    """B5: constructing a real SDK client, or sending an HTTP request, is a failure."""

    def _boom(*_a, **_k):
        raise AssertionError("a real provider client or HTTP request was used in a test")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", _boom)
    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", _boom)
    monkeypatch.setattr(genai.Client, "__init__", _boom)
    monkeypatch.setattr(httpx.Client, "send", _boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", _boom)


@pytest.fixture(autouse=True)
def _isolated_bakeoff_root(tmp_path, monkeypatch):
    """Runs under the real backend/.bakeoff/ never count toward a test's cap."""
    monkeypatch.setattr(eb, "BAKEOFF_ROOT", tmp_path / "bakeoff-root")
    # No Sonnet subset file unless a test writes one.
    monkeypatch.setattr(eb, "SONNET_SAMPLE_PATH", tmp_path / "no-sonnet-sample.json")


# --------------------------------------------------------------------------- fixtures


def _row(doc_id, doc_kind, comm_type, tokens_in=1000, method="pymupdf"):
    return {
        "doc_id": doc_id,
        "nhtsa_id": f"NH{doc_id}",
        "url": f"https://static.nhtsa.gov/odi/tsbs/MC-{doc_id}.pdf",
        "doc_kind": doc_kind,
        "communication_type": comm_type,
        "llm_tokens_in": tokens_in,
        "llm_tokens_out": 250,
        "page_count": 3,
        "extraction_method": method,
    }


def _corpus():
    """600 documents across five cells of very different size, plus the forced cases."""
    rows, next_id = [], 1
    for cell, n in {
        ("informational", "PIT"): 320,
        ("service_procedure", "TSB"): 200,
        ("warranty_admin", "WA"): 60,
        ("customer_letter", "CSP"): 6,
        ("recall", "DL"): 2,  # below the per-cell floor: has no guarantee
    }.items():
        for _ in range(n):
            rows.append(_row(next_id, cell[0], cell[1], tokens_in=500 + next_id))
            next_id += 1
    # Two vision-fallback documents (no text, so no token count) and one huge document.
    rows.append(_row(9001, None, "MC", tokens_in=None, method="vision-fallback"))
    rows.append(_row(9002, "informational", "MC", tokens_in=None, method="vision-fallback"))
    random.Random(1).shuffle(rows)
    return rows


def _fake_doc(doc_id=7, **extra):
    base = {
        "doc_id": doc_id,
        "nhtsa_id": f"NH{doc_id}",
        "url": f"https://static.nhtsa.gov/odi/tsbs/MC-{doc_id}.pdf",
        "text": "Customer states the rear hatch will not latch.",
        "llm_tokens_in": 1200,
        "llm_tokens_out": 240,
        "page_count": 2,
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------- (a) sampling


def test_the_sample_is_deterministic_and_covers_every_large_cell():
    rows = _corpus()

    first = eb.select_sample(rows, size=100)
    second = eb.select_sample(list(reversed(rows)), size=100)

    assert [r["doc_id"] for r in first] == [r["doc_id"] for r in second]
    assert len(first) == len({r["doc_id"] for r in first}) == 100
    cells = Counter((r["doc_kind"], r["communication_type"]) for r in first)
    for cell in [
        ("informational", "PIT"),
        ("service_procedure", "TSB"),
        ("warranty_admin", "WA"),
        ("customer_letter", "CSP"),
    ]:
        assert cells[cell] >= 5, f"cell {cell} has fewer than 5 sampled documents"


def test_the_sample_always_holds_vision_fallback_and_the_largest_documents():
    rows = _corpus()

    sample = eb.select_sample(rows, size=100)

    ids = {r["doc_id"] for r in sample}
    assert {9001, 9002} <= ids
    largest = sorted(
        (r for r in rows if r["llm_tokens_in"] is not None),
        key=lambda r: (-r["llm_tokens_in"], r["doc_id"]),
    )[:10]
    assert {r["doc_id"] for r in largest} <= ids


def test_the_sample_is_proportional_beyond_the_floors():
    sample = eb.select_sample(_corpus(), size=100)

    cells = Counter((r["doc_kind"], r["communication_type"]) for r in sample)

    assert cells[("informational", "PIT")] > cells[("service_procedure", "TSB")]
    assert cells[("service_procedure", "TSB")] > cells[("warranty_admin", "WA")]


def test_sample_rows_carry_exactly_the_recorded_fields():
    sample = eb.select_sample(_corpus(), size=100)

    assert set(sample[0]) == {
        "doc_id",
        "nhtsa_id",
        "url",
        "doc_kind",
        "communication_type",
        "llm_tokens_in",
        "llm_tokens_out",
        "page_count",
    }


async def test_sample_writes_the_same_bytes_on_every_run(tmp_path, monkeypatch):
    async def fake_candidates():
        return _corpus()

    monkeypatch.setattr(eb, "_fetch_candidates", fake_candidates)
    out = tmp_path / "sample.json"

    await eb.write_sample(out, size=100)
    first = out.read_bytes()
    await eb.write_sample(out, size=100)

    assert out.read_bytes() == first
    assert len(json.loads(first)) == 100


# --------------------------------------------------------------------------- (b) request shapes


def _pdf_doc(doc_id=7, payload=b"%PDF-1.4 fake\n" * 40):
    return _fake_doc(doc_id, pdf_b64=base64.standard_b64encode(payload).decode("ascii"))


def test_the_gemini_arm_builds_a_text_request_at_temperature_zero_with_the_production_schema():
    [req] = eb.build_requests("G", [_fake_doc()])

    assert req.custom_id == "7"
    assert req.params.model == "gemini-3.5-flash-lite"
    assert req.params.contents == llm.TEXT_PROMPT.format(text=_fake_doc()["text"])
    assert req.params.config.temperature == 0
    assert req.params.config.response_schema == llm.RESPONSE_SCHEMA
    assert req.params.config.response_mime_type == "application/json"
    assert req.params.metadata == {"doc_id": "7"}


def test_the_haiku_text_arm_builds_a_low_effort_structured_request_without_temperature():
    [req] = eb.build_requests("H-text", [_fake_doc()])

    params = req.params
    assert req.custom_id == "7"
    assert params["model"] == "claude-haiku-5-5"
    assert params["max_tokens"] == 8192
    assert params["output_config"]["effort"] == "low"
    fmt = params["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["required"] == llm.RESPONSE_SCHEMA["required"]
    assert fmt["schema"]["additionalProperties"] is False
    assert "temperature" not in params
    assert params["messages"] == [
        {"role": "user", "content": llm.TEXT_PROMPT.format(text=_fake_doc()["text"])}
    ]


def test_the_anthropic_schema_copy_leaves_the_production_schema_untouched():
    before = copy.deepcopy(llm.RESPONSE_SCHEMA)

    eb.build_requests("H-text", [_fake_doc()])

    assert llm.RESPONSE_SCHEMA == before
    assert "additionalProperties" not in llm.RESPONSE_SCHEMA


def test_the_haiku_pdf_arm_sends_the_pdf_as_one_unbroken_base64_document_block():
    doc = _pdf_doc()

    [req] = eb.build_requests("H-pdf", [doc])

    params = req.params
    assert "temperature" not in params
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"]["effort"] == "low"
    [message] = params["messages"]
    document, text = message["content"]
    assert document["type"] == "document"
    assert document["source"]["type"] == "base64"
    assert document["source"]["media_type"] == "application/pdf"
    assert document["source"]["data"] == doc["pdf_b64"]
    assert "\n" not in document["source"]["data"]
    assert text["type"] == "text"
    assert "attached" in text["text"] and "PDF" in text["text"]
    assert llm._RULES in text["text"]


def test_a_document_without_its_text_or_pdf_becomes_an_error_row_instead_of_a_request():
    texts = eb.build_requests("H-text", [_fake_doc(8, text=None)])
    pdfs = eb.build_requests("H-pdf", [_fake_doc(9, pdf_b64=None, pdf_error="pdf unavailable")])

    assert texts[0].params is None and texts[0].error == "no extracted text"
    assert pdfs[0].params is None and pdfs[0].error == "pdf unavailable"


def test_no_anthropic_request_carries_temperature():
    docs = [_pdf_doc(1), _pdf_doc(2)]

    requests = eb.build_requests("H-text", docs) + eb.build_requests("H-pdf", docs)

    assert requests and all("temperature" not in r.params for r in requests)


# --------------------------------------------------------------------------- run harness

GOOD = {
    "summary": "Rear hatch latch may not close.",
    "symptoms": ["hatch will not latch"],
    "systems": ["body"],
    "components": ["latch"],
    "remedy": "Replace latch.",
    "applicability": "2021 Example Truck",
    "doc_kind": "service_procedure",
}


def _sample_entry(doc_id, pages=2, tokens_in=1000, tokens_out=200):
    return {
        "doc_id": doc_id,
        "nhtsa_id": f"NH{doc_id}",
        "url": f"https://static.nhtsa.gov/odi/tsbs/MC-{doc_id}.pdf",
        "doc_kind": "informational",
        "communication_type": "PIT",
        "llm_tokens_in": tokens_in,
        "llm_tokens_out": tokens_out,
        "page_count": pages,
    }


class FakeAnthropic:
    """Stands in for ``anthropic.Anthropic``: records batches, replays canned results."""

    def __init__(self, results=(), status="ended"):
        self.created = []
        self._results = list(results)
        self._status = status
        self.messages = SimpleNamespace(
            batches=SimpleNamespace(
                create=self._create, retrieve=self._retrieve, results=self._results_for
            )
        )

    def _create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def _retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self._status)

    def _results_for(self, batch_id):
        return iter(self._results)


def _succeeded(doc_id, text, tin=1500, tout=220, stop="end_turn", leading_thinking=False):
    content = [SimpleNamespace(type="text", text=text)]
    if leading_thinking:
        content.insert(0, SimpleNamespace(type="thinking", thinking="..."))
    message = SimpleNamespace(
        content=content,
        usage=SimpleNamespace(input_tokens=tin, output_tokens=tout),
        stop_reason=stop,
    )
    return SimpleNamespace(
        custom_id=str(doc_id), result=SimpleNamespace(type="succeeded", message=message)
    )


def _anthropic_failure(doc_id, kind="errored"):
    error = SimpleNamespace(type="invalid_request") if kind == "errored" else None
    return SimpleNamespace(custom_id=str(doc_id), result=SimpleNamespace(type=kind, error=error))


class FakeGemini:
    def __init__(self, responses=(), state="JOB_STATE_SUCCEEDED"):
        self.created = []
        self._responses = list(responses)
        self._state = state
        self.batches = SimpleNamespace(create=self._create, get=self._get)

    def _create(self, model, src, config):
        self.created.append(list(src))
        return SimpleNamespace(name=f"batches/g{len(self.created)}")

    def _get(self, name):
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name=self._state),
            dest=SimpleNamespace(inlined_responses=self._responses),
        )


def _gemini_item(doc_id, text, tin=1400, tout=210):
    return SimpleNamespace(
        metadata={"doc_id": str(doc_id)},
        error=None,
        response=SimpleNamespace(
            text=text,
            usage_metadata=SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout),
        ),
    )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A sample file, a run directory and faked network edges. No SDK client is real."""
    entries = [_sample_entry(i) for i in (1, 2, 3)]
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(entries))
    run_dir = tmp_path / "run"

    usage = []

    async def fake_record_usage(kind, model, **kwargs):
        usage.append({"kind": kind, "model": model, **kwargs})
        return 0.0

    async def fake_texts(doc_ids):
        return {i: f"Bulletin text for document {i}." for i in doc_ids}

    async def fake_pdfs(sample):
        return {
            e["doc_id"]: (base64.standard_b64encode(b"%PDF-1.4 " + bytes(40)).decode(), None)
            for e in sample
        }

    monkeypatch.setattr(eb, "record_usage", fake_record_usage)
    monkeypatch.setattr(eb, "_fetch_texts", fake_texts)
    monkeypatch.setattr(eb, "_fetch_pdfs", fake_pdfs)

    return SimpleNamespace(
        entries=entries,
        sample_path=sample_path,
        run_dir=run_dir,
        usage=usage,
        monkeypatch=monkeypatch,
        use=lambda anthropic_client=None, gemini_client=None: (
            monkeypatch.setattr(eb, "_anthropic_client", lambda: anthropic_client),
            monkeypatch.setattr(eb, "_gemini_client", lambda: gemini_client),
        ),
    )


def _results(run_dir, arm):
    path = run_dir / arm / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


# --------------------------------------------------------------------------- (c) collect


async def test_collect_turns_haiku_results_into_one_validated_line_per_document(harness):
    client = FakeAnthropic(
        results=[
            _succeeded(3, json.dumps(GOOD), tin=900, tout=100),
            _succeeded(1, json.dumps(GOOD), tin=1500, tout=220, leading_thinking=True),
            _succeeded(2, '{"summary": "cut off mid-sen', tin=800, tout=2048, stop="max_tokens"),
        ]
    )
    harness.use(anthropic_client=client)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)

    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "H-text")}
    assert sorted(lines) == [1, 2, 3]
    assert lines[1]["valid"] is True and lines[1]["error"] is None
    assert lines[1]["output"]["summary"] == GOOD["summary"]
    assert lines[2]["valid"] is False and lines[2]["output"] is None
    assert "invalid JSON" in lines[2]["error"] and "max_tokens" in lines[2]["error"]
    for line in lines.values():
        assert line["arm"] == "H-text" and line["model"] == "claude-haiku-5-5"
        assert line["nhtsa_id"] == f"NH{line['doc_id']}"
        assert line["collected_at"]
    assert (lines[1]["tokens_in"], lines[1]["tokens_out"]) == (1500, 220)
    assert lines[1]["cost_usd"] == pytest.approx(pricing_cost("claude-haiku-5-5", 1500, 220))


def pricing_cost(model, tin, tout):
    from src.usage import pricing

    return pricing.cost_of(model, tin, tout, batch=True)


async def test_collect_records_usage_once_per_response_including_malformed_ones(harness):
    client = FakeAnthropic(
        results=[
            _succeeded(1, json.dumps(GOOD), tin=1500, tout=220),
            _succeeded(2, "not json at all", tin=700, tout=30),
            _succeeded(3, json.dumps({"summary": "x"}), tin=650, tout=40),
        ]
    )
    harness.use(anthropic_client=client)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)

    assert len(harness.usage) == 3
    by_tokens = {u["tokens_in"]: u for u in harness.usage}
    first = by_tokens[1500]
    assert first["kind"] == "llm_extract"
    assert first["model"] == "claude-haiku-5-5"
    assert first["tokens_out"] == 220
    assert first["batch"] is True
    assert first["meta"]["bakeoff"] == "H-text"
    assert first["meta"]["run"] == str(harness.run_dir)
    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "H-text")}
    assert lines[3]["valid"] is False and "missing fields" in lines[3]["error"]


async def test_collect_gives_every_submitted_document_a_line_even_without_a_response(harness):
    client = FakeAnthropic(
        results=[_succeeded(1, json.dumps(GOOD)), _anthropic_failure(2, "expired")]
    )  # document 3 never comes back
    harness.use(anthropic_client=client)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)

    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "H-text")}
    assert sorted(lines) == [1, 2, 3]
    assert lines[2]["valid"] is False and "expired" in lines[2]["error"]
    assert lines[3]["valid"] is False and "no result" in lines[3]["error"]
    assert len(harness.usage) == 1, "only the response that was returned was billed"


async def test_collect_turns_a_gemini_job_into_results_and_usage(harness):
    gemini = FakeGemini(
        responses=[
            _gemini_item(2, json.dumps(GOOD), tin=1400, tout=210),
            _gemini_item(1, "```json oops```", tin=1300, tout=15),
            _gemini_item(3, json.dumps(GOOD)),
        ]
    )
    harness.use(gemini_client=gemini)
    await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)

    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "G")}
    assert sorted(lines) == [1, 2, 3]
    assert lines[2]["valid"] is True and lines[2]["model"] == "gemini-3.5-flash-lite"
    assert lines[1]["valid"] is False and lines[1]["error"]
    assert (lines[2]["tokens_in"], lines[2]["tokens_out"]) == (1400, 210)
    assert len(harness.usage) == 3
    assert {u["model"] for u in harness.usage} == {"gemini-3.5-flash-lite"}
    assert all(u["batch"] is True and u["meta"]["bakeoff"] == "G" for u in harness.usage)


async def test_collect_survives_line_separator_characters_inside_a_document_and_a_truncated_response(
    harness,
):
    async def texts(doc_ids):
        return {i: f"Step one\x85step two\u2028step three {i}." for i in doc_ids}

    harness.monkeypatch.setattr(eb, "_fetch_texts", texts)
    odd = dict(GOOD, summary="Latch\x85fails\u2028often.")
    gemini = FakeGemini(
        responses=[
            _gemini_item(1, json.dumps(odd, ensure_ascii=False)),
            _gemini_item(2, '{"summary": "Rear hatch may not cl', tin=1300, tout=8192),
            _gemini_item(3, json.dumps(GOOD)),
        ]
    )
    harness.use(gemini_client=gemini)
    await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)

    report = await eb.collect(harness.run_dir)

    assert report["G"] == "collected 3 (2 valid)"
    lines = {r["doc_id"]: r for r in eb._read_jsonl(harness.run_dir / "G" / "results.jsonl")}
    assert lines[1]["valid"] is True and lines[1]["output"]["summary"] == odd["summary"]
    assert lines[2]["valid"] is False and "invalid JSON" in lines[2]["error"]
    assert lines[2]["tokens_out"] == 8192
    assert len(harness.usage) == 3
    assert "collected 3 (2 valid)" in eb.status(harness.run_dir, harness.sample_path)


async def test_a_pdf_that_nhtsa_no_longer_serves_is_recorded_not_requested(harness):
    async def pdfs(sample):
        ok = base64.standard_b64encode(b"%PDF-1.4 x").decode()
        return {1: (ok, None), 2: (None, "pdf unavailable"), 3: (ok, None)}

    harness.monkeypatch.setattr(eb, "_fetch_pdfs", pdfs)
    client = FakeAnthropic(
        results=[_succeeded(1, json.dumps(GOOD)), _succeeded(3, json.dumps(GOOD))]
    )
    harness.use(anthropic_client=client)

    await eb.submit("H-pdf", harness.run_dir, sample_path=harness.sample_path)
    await eb.collect(harness.run_dir)

    assert [r["custom_id"] for r in client.created[0]] == ["1", "3"]
    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "H-pdf")}
    assert sorted(lines) == [1, 2, 3]
    assert lines[2]["error"] == "pdf unavailable" and lines[2]["valid"] is False
    assert len(harness.usage) == 2


async def test_collect_waits_for_unfinished_batches_and_never_records_twice(harness):
    client = FakeAnthropic(
        results=[_succeeded(i, json.dumps(GOOD)) for i in (1, 2, 3)], status="in_progress"
    )
    harness.use(anthropic_client=client)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)
    assert not (harness.run_dir / "H-text" / "results.jsonl").exists()
    assert harness.usage == []

    client._status = "ended"
    await eb.collect(harness.run_dir)
    await eb.collect(harness.run_dir)

    assert len(_results(harness.run_dir, "H-text")) == 3
    assert len(harness.usage) == 3


# --------------------------------------------------------------------------- submit plumbing


async def test_submit_records_requests_and_every_batch_id(harness):
    client = FakeAnthropic()
    harness.use(anthropic_client=client)
    harness.monkeypatch.setattr(eb, "ANTHROPIC_BATCH_BYTES", 1)  # one request per batch

    ids = await eb.submit("H-pdf", harness.run_dir, sample_path=harness.sample_path)

    assert ids == {"H-pdf": ["msgbatch_1", "msgbatch_2", "msgbatch_3"]}
    batch = json.loads((harness.run_dir / "H-pdf" / "batch.json").read_text())
    assert [b["id"] for b in batch["batches"]] == ids["H-pdf"]
    assert batch["submitted_at"] and batch["model"] == "claude-haiku-5-5"
    recorded = [
        json.loads(line)
        for line in (harness.run_dir / "H-pdf" / "requests.jsonl").read_text().splitlines()
    ]
    assert [r["doc_id"] for r in recorded] == [1, 2, 3]
    pdf_source = recorded[0]["request"]["messages"][0]["content"][0]["source"]
    assert pdf_source["data"].startswith("<base64 ")


async def test_gemini_requests_go_out_in_chunks_of_one_hundred(harness):
    entries = [_sample_entry(i) for i in range(1, 251)]
    harness.sample_path.write_text(json.dumps(entries))
    gemini = FakeGemini()
    harness.use(gemini_client=gemini)

    await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)

    assert [len(c) for c in gemini.created] == [100, 100, 50]
    batch = json.loads((harness.run_dir / "G" / "batch.json").read_text())
    assert [b["id"] for b in batch["batches"]] == ["batches/g1", "batches/g2", "batches/g3"]


async def test_submit_names_a_missing_key_instead_of_a_sdk_traceback(harness):
    def no_key():
        raise RuntimeError(
            "CLAUDE_API_KEY (or ANTHROPIC_API_KEY) is not set. Add it to backend/.env."
        )

    harness.monkeypatch.setattr(eb, "_anthropic_client", no_key)

    with pytest.raises(RuntimeError, match="CLAUDE_API_KEY"):
        await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)
    assert not (harness.run_dir / "H-text" / "batch.json").exists()


# --------------------------------------------------------------------------- (d) spend cap


async def test_submit_refuses_when_the_estimate_would_pass_the_cap_and_spends_nothing(harness):
    anthropic_client, gemini = FakeAnthropic(), FakeGemini()
    harness.use(anthropic_client=anthropic_client, gemini_client=gemini)
    harness.sample_path.write_text(
        json.dumps([_sample_entry(i, tokens_in=500_000) for i in (1, 2, 3)])
    )
    harness.monkeypatch.setattr(eb, "CAP_USD", 0.01)
    harness.monkeypatch.setattr(
        eb, "_fetch_pdfs", lambda s: pytest.fail("a refused submit must not download PDFs")
    )

    with pytest.raises(eb.SpendCapExceeded) as refusal:
        await eb.submit("all", harness.run_dir, sample_path=harness.sample_path)

    assert "$0.01" in str(refusal.value) and "H-pdf" in str(refusal.value)
    assert anthropic_client.created == [] and gemini.created == []
    assert not list(harness.run_dir.glob("*/batch.json"))


def test_status_reports_the_estimate_for_every_arm(harness, capsys):
    harness.monkeypatch.setattr(eb, "CAP_USD", 0.01)
    entries = [_sample_entry(i, pages=2, tokens_in=1000, tokens_out=200) for i in (1, 2, 3)]
    harness.sample_path.write_text(json.dumps(entries))

    code = eb.main(
        ["status", "--run-dir", str(harness.run_dir), "--sample", str(harness.sample_path)]
    )

    out = capsys.readouterr().out
    assert code == 0
    gemini_estimate = 3 * pricing_cost("gemini-3.5-flash-lite", 1000, 200)
    haiku_text_estimate = 3 * pricing_cost("claude-haiku-5-5", 1000, 200)
    pdf_estimate = 3 * pricing_cost("claude-haiku-5-5", 2 * 3000, 300)
    assert eb.estimate_cost("G", entries) == pytest.approx(gemini_estimate)
    assert eb.estimate_cost("H-text", entries) == pytest.approx(haiku_text_estimate)
    assert eb.estimate_cost("H-pdf", entries) == pytest.approx(pdf_estimate)
    for arm in ("G", "H-text", "H-pdf"):
        assert arm in out
    assert f"${gemini_estimate:.4f}" in out
    assert f"${pdf_estimate:.4f}" in out
    assert "not submitted" in out
    assert "cap $0.01" in out


async def test_spend_already_collected_in_the_run_counts_against_the_cap(harness):
    haiku = FakeAnthropic(
        results=[_succeeded(i, json.dumps(GOOD), tin=1500, tout=220) for i in (1, 2, 3)]
    )
    gemini = FakeGemini()
    harness.use(anthropic_client=haiku, gemini_client=gemini)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)
    await eb.collect(harness.run_dir)
    spent = eb.run_spend(harness.run_dir).collected
    assert spent == pytest.approx(3 * pricing_cost("claude-haiku-5-5", 1500, 220))

    gemini_estimate = eb.estimate_cost("G", harness.entries)
    harness.monkeypatch.setattr(eb, "CAP_USD", spent + gemini_estimate * 0.5)
    with pytest.raises(eb.SpendCapExceeded):
        await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)
    assert gemini.created == []

    harness.monkeypatch.setattr(eb, "CAP_USD", spent + gemini_estimate * 2)
    await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)
    assert len(gemini.created) == 1


def test_status_lists_batch_ids_and_actual_spend_after_a_run(harness, capsys):
    import asyncio

    haiku = FakeAnthropic(
        results=[_succeeded(i, json.dumps(GOOD), tin=1500, tout=220) for i in (1, 2, 3)]
    )
    harness.use(anthropic_client=haiku)

    async def go():
        await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)
        await eb.collect(harness.run_dir)

    asyncio.run(go())

    eb.main(["status", "--run-dir", str(harness.run_dir), "--sample", str(harness.sample_path)])

    out = capsys.readouterr().out
    assert "msgbatch_1" in out
    assert "collected 3" in out
    assert f"${3 * pricing_cost('claude-haiku-5-5', 1500, 220):.4f}" in out


# --------------------------------------------------------------------------- PDF intake


async def test_pdfs_come_from_the_cached_download_as_unbroken_base64(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 50  # base64 of this would wrap at 76 columns in a MIME encoder
    small = tmp_path / "small.pdf"
    small.write_bytes(payload)

    async def fake_download(http, url):
        return small, None, True

    monkeypatch.setattr(eb.extract, "download", fake_download)

    out = await eb._fetch_pdfs([_sample_entry(1)])

    data, error = out[1]
    assert error is None and "\n" not in data
    assert base64.standard_b64decode(data) == payload


async def test_a_missing_or_oversized_pdf_is_an_error_not_a_request(tmp_path, monkeypatch):
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x" * 64)
    monkeypatch.setattr(eb, "MAX_PDF_BYTES", 32)

    async def fake_download(http, url):
        if url.endswith("-1.pdf"):
            return None, "http 404 (document not published)", False
        return big, None, False

    monkeypatch.setattr(eb.extract, "download", fake_download)

    out = await eb._fetch_pdfs([_sample_entry(1), _sample_entry(2)])

    assert out[1] == (None, "pdf unavailable")
    assert out[2][0] is None and "too large" in out[2][1]


# --------------------------------------------------------------------------- review fixes


async def test_a_failed_later_batch_keeps_the_paid_ones_and_a_retry_never_resends_them(harness):
    entries = [_sample_entry(i) for i in range(1, 251)]
    harness.sample_path.write_text(json.dumps(entries))
    gemini = FakeGemini()
    real_create = gemini._create

    def create_then_fail(model, src, config):
        if len(gemini.created) == 2:
            raise RuntimeError("503 from the batch endpoint")
        return real_create(model, src, config)

    gemini.batches.create = create_then_fail
    harness.use(gemini_client=gemini)

    with pytest.raises(RuntimeError, match="503"):
        await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)

    batch = json.loads((harness.run_dir / "G" / "batch.json").read_text())
    assert [b["id"] for b in batch["batches"]] == ["batches/g1", "batches/g2"]
    with pytest.raises(RuntimeError, match="already submitted"):
        await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)
    assert len(gemini.created) == 2  # the two paid batches were never sent again


async def test_spend_in_another_run_of_the_bakeoff_counts_against_the_cap(harness):
    other = eb.BAKEOFF_ROOT / "20261101-0900" / "H-text"
    other.mkdir(parents=True)
    (other / "batch.json").write_text(json.dumps({"estimate_usd": 0.0, "batches": []}))
    (other / "results.jsonl").write_text(json.dumps({"cost_usd": eb.CAP_USD - 0.01}) + "\n")
    (eb.BAKEOFF_ROOT / "20261101-0900" / "judge.jsonl").write_text(
        json.dumps({"cost_usd": 0.5}) + "\n"
    )
    gemini = FakeGemini()
    harness.use(gemini_client=gemini)

    assert eb.experiment_spend(harness.run_dir) == pytest.approx(eb.CAP_USD + 0.49)
    with pytest.raises(eb.SpendCapExceeded, match="across the bake-off"):
        await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)
    assert gemini.created == []


def test_submit_from_the_cli_reuses_the_newest_run_so_an_arm_is_never_sent_twice(harness, capsys):
    client = FakeAnthropic()
    harness.use(anthropic_client=client)
    # Each invocation a minute apart, as two commands typed by hand would be.
    minutes = iter(["20261107-0900", "20261107-0901"])
    harness.monkeypatch.setattr(eb, "new_run_dir", lambda: eb.BAKEOFF_ROOT / next(minutes))
    argv = ["submit", "--arm", "H-text", "--sample", str(harness.sample_path)]

    assert eb.main(argv) == 0
    assert eb.main(argv) == 2
    assert "already submitted" in capsys.readouterr().err
    assert len(client.created) == 1
    assert len([p for p in eb.BAKEOFF_ROOT.iterdir() if p.is_dir()]) == 1


async def test_gemini_thinking_tokens_are_counted_and_costed_as_output(harness):
    item = _gemini_item(1, json.dumps(GOOD), tin=1400, tout=210)
    item.response.usage_metadata.thoughts_token_count = 500
    harness.use(gemini_client=FakeGemini(responses=[item]))
    harness.sample_path.write_text(json.dumps([_sample_entry(1)]))

    await eb.submit("G", harness.run_dir, sample_path=harness.sample_path)
    await eb.collect(harness.run_dir)

    [line] = _results(harness.run_dir, "G")
    assert line["tokens_out"] == 710
    assert line["cost_usd"] == pytest.approx(pricing_cost("gemini-3.5-flash-lite", 1400, 710))
    assert harness.usage[0]["tokens_out"] == 710


async def test_an_errored_anthropic_result_names_the_real_reason_and_is_not_billed(harness):
    from anthropic.types import ErrorResponse, InvalidRequestError
    from anthropic.types.messages import MessageBatchErroredResult

    errored = SimpleNamespace(
        custom_id="1",
        result=MessageBatchErroredResult(
            type="errored",
            error=ErrorResponse(
                type="error",
                error=InvalidRequestError(type="invalid_request_error", message="bad schema"),
            ),
        ),
    )
    harness.use(anthropic_client=FakeAnthropic(results=[errored]))
    harness.sample_path.write_text(json.dumps([_sample_entry(1)]))

    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)
    await eb.collect(harness.run_dir)

    [line] = _results(harness.run_dir, "H-text")
    assert line["error"] == "batch errored: invalid_request_error (bad schema)"
    assert line["cost_usd"] == 0.0 and harness.usage == []


def test_the_largest_pdf_allowed_still_fits_a_request_once_base64_encoded():
    request_limit = 32 * 1024 * 1024
    base64_bytes = -(-eb.MAX_PDF_BYTES // 3) * 4
    assert base64_bytes + 256 * 1024 < request_limit


# --------------------------------------------------------------------------- Sonnet subset


def test_the_sonnet_subset_is_one_hundred_deterministic_rows_drawn_from_the_sample():
    sample = eb.load_sample(eb.SAMPLE_PATH)

    first = eb.select_sonnet_subset(sample)
    second = eb.select_sonnet_subset(list(reversed(sample)))

    assert [r["doc_id"] for r in first] == [r["doc_id"] for r in second]
    assert len(first) == len({r["doc_id"] for r in first}) == 100
    by_id = {r["doc_id"]: r for r in sample}
    assert all(by_id[r["doc_id"]] == r for r in first), "same row shape, same values"
    # every (doc_kind, communication_type) cell that has a usable document is represented
    usable = {
        (r["doc_kind"], r["communication_type"]) for r in sample if r["llm_tokens_in"] is not None
    }
    assert {(r["doc_kind"], r["communication_type"]) for r in first} == usable
    assert all(r["llm_tokens_in"] is not None and r["url"] for r in first)


def test_the_sonnet_subset_shares_cells_in_proportion_to_the_sample():
    sample = eb.load_sample(eb.SAMPLE_PATH)

    subset = eb.select_sonnet_subset(sample)

    big = Counter((r["doc_kind"], r["communication_type"]) for r in subset)
    assert (
        big[("informational", "PIT")] > big[("informational", "MC")] > big[("warranty_admin", "WA")]
    )


def test_the_sonnet_subset_prefers_documents_with_text_and_a_url_and_floors_each_cell_at_one():
    rows = [_row(i, "informational", "PIT") for i in range(1, 41)]
    rows += [_row(100, "recall", "DL"), _row(101, "other", "MC")]
    rows.append({**_row(200, "other", "MC"), "llm_tokens_in": None})  # no text
    rows.append({**_row(201, "other", "BL"), "url": ""})  # no url

    subset = eb.select_sonnet_subset(rows, size=10)

    ids = {r["doc_id"] for r in subset}
    assert len(ids) == 10 and {100, 101} <= ids
    assert not ids & {200, 201}


def test_sample_sonnet_writes_the_subset_file_and_it_is_the_one_committed(tmp_path, capsys):
    out = tmp_path / "subset.json"

    code = eb.main(["sample-sonnet", "--sample", str(eb.SAMPLE_PATH), "--out", str(out)])

    assert code == 0
    assert "wrote 100 documents" in capsys.readouterr().out
    committed = eb.SAMPLE_PATH.with_name("extraction_sample_sonnet100.json")
    assert out.read_bytes() == committed.read_bytes(), "regenerate the committed subset"


# --------------------------------------------------------------------------- live arms


def _message(text, tin=1500, tout=220, stop="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tin, output_tokens=tout),
        stop_reason=stop,
    )


def _http_error(cls, status, message="boom"):
    request = httpx.Request("POST", "https://api.anthropic.test/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=None)


class FakeLive:
    """Stands in for ``anthropic.Anthropic`` on the synchronous Messages API."""

    def __init__(self, reply=None, on_call=None):
        self.calls = []
        self._reply = reply or (lambda params: _message(json.dumps(GOOD)))
        self._on_call = on_call
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        if self._on_call:
            self._on_call(len(self.calls), params)
        return self._reply(params)


@pytest.fixture
def live(harness, tmp_path):
    """``harness`` plus the Sonnet subset file, recorded sleeps and one-at-a-time calls."""
    sonnet_path = tmp_path / "sonnet.json"
    sonnet_path.write_text(json.dumps(harness.entries[:2]))
    sleeps = []
    harness.monkeypatch.setattr(eb, "_sleep", sleeps.append)
    harness.sonnet_path, harness.sleeps = sonnet_path, sleeps

    async def run(arm, client, **kw):
        harness.use(anthropic_client=client)
        return await eb.run_live(
            arm,
            harness.run_dir,
            sample_path=harness.sample_path,
            sonnet_sample_path=sonnet_path,
            concurrency=kw.pop("concurrency", 1),
            **kw,
        )

    harness.run_live = run
    return harness


async def test_a_live_arm_writes_each_finished_document_at_once_and_a_rerun_sends_nothing(live):
    seen = []

    def on_call(n, params):
        path = live.run_dir / "H-text-live" / "results.jsonl"
        seen.append(len(path.read_text().splitlines()) if path.exists() else 0)

    client = FakeLive(on_call=on_call)

    await live.run_live("H-text-live", client)

    assert seen == [0, 1, 2], "each result is on disk before the next call starts"
    lines = _results(live.run_dir, "H-text-live")
    assert sorted(r["doc_id"] for r in lines) == [1, 2, 3]
    first = lines[0]
    assert first["arm"] == "H-text-live" and first["model"] == "claude-haiku-5-5"
    assert first["mode"] == "live" and first["valid"] is True and first["error"] is None
    assert (first["tokens_in"], first["tokens_out"]) == (1500, 220)
    assert first["cost_usd"] == pytest.approx(
        eb.pricing.cost_of("claude-haiku-5-5", 1500, 220, batch=False)
    ), "live calls are costed at list price"
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["max_tokens"] == 8192
    assert call["output_config"]["effort"] == "low" and "temperature" not in call
    assert "thinking" not in call

    again = FakeLive()
    out = await live.run_live("H-text-live", again)

    assert again.calls == []
    assert len(_results(live.run_dir, "H-text-live")) == 3
    assert len(live.usage) == 3, "the re-run recorded nothing twice"
    assert out["H-text-live"].sent == 0 and out["H-text-live"].lines == 3


async def test_a_live_arm_resumes_a_crashed_run_without_resending_finished_documents(live):
    class Crash(Exception):
        pass

    def crash_on_third(n, params):
        if n == 3:
            raise Crash

    with pytest.raises(Crash):
        await live.run_live("H-text-live", FakeLive(on_call=crash_on_third))
    assert sorted(r["doc_id"] for r in _results(live.run_dir, "H-text-live")) == [1, 2]
    assert (live.run_dir / "H-text-live" / "live.json").exists(), "wall time kept on a crash"

    client = FakeLive()
    await live.run_live("H-text-live", client)

    assert len(client.calls) == 1
    assert sorted(r["doc_id"] for r in _results(live.run_dir, "H-text-live")) == [1, 2, 3]


async def test_each_billed_live_result_is_recorded_once_at_list_price_with_the_live_marker(live):
    replies = iter(
        [
            _message(json.dumps(GOOD), tin=1500, tout=220),
            _message("not json at all", tin=700, tout=30),  # still billed
            _message(json.dumps(GOOD), tin=650, tout=40),
        ]
    )

    await live.run_live("H-text-live", FakeLive(reply=lambda p: next(replies)))

    assert len(live.usage) == 3
    first = live.usage[0]
    assert first["kind"] == "llm_extract" and first["model"] == "claude-haiku-5-5"
    assert first["batch"] is False
    assert first["meta"] == {
        "bakeoff": "H-text-live",
        "run": str(live.run_dir),
        "doc_id": 1,
        "mode": "live",
    }
    lines = {r["doc_id"]: r for r in _results(live.run_dir, "H-text-live")}
    assert lines[2]["valid"] is False and "invalid JSON" in lines[2]["error"]
    assert lines[2]["cost_usd"] > 0


async def test_a_retryable_error_is_retried_with_backoff_and_then_succeeds(live):
    failures = [
        _http_error(anthropic.RateLimitError, 429),
        _http_error(anthropic.InternalServerError, 529, "overloaded_error"),
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.test")),
    ]

    def on_call(n, params):
        if n <= len(failures):
            raise failures[n - 1]

    live.sample_path.write_text(json.dumps(live.entries[:1]))
    client = FakeLive(on_call=on_call)

    await live.run_live("H-text-live", client)

    [line] = _results(live.run_dir, "H-text-live")
    assert line["valid"] is True and len(client.calls) == 4
    assert live.sleeps == [2.0, 4.0, 8.0], "exponential backoff between tries"
    assert len(live.usage) == 1, "only the response that came back was billed"


async def test_a_call_that_keeps_failing_is_left_for_the_next_run_not_recorded(live):
    live.sample_path.write_text(json.dumps(live.entries[:1]))

    def always(n, params):
        raise _http_error(anthropic.RateLimitError, 429)

    out = await live.run_live("H-text-live", FakeLive(on_call=always))

    assert not (live.run_dir / "H-text-live" / "results.jsonl").exists() or not _results(
        live.run_dir, "H-text-live"
    )
    assert out["H-text-live"].retry_later == 1 and live.usage == []

    client = FakeLive()
    await live.run_live("H-text-live", client)
    assert len(client.calls) == 1 and len(_results(live.run_dir, "H-text-live")) == 1


async def test_a_non_retryable_api_error_becomes_an_unbilled_error_line(live):
    def reject_second(n, params):
        if n == 2:
            raise _http_error(anthropic.BadRequestError, 400, "schema not supported")

    client = FakeLive(on_call=reject_second)

    await live.run_live("H-text-live", client)

    lines = {r["doc_id"]: r for r in _results(live.run_dir, "H-text-live")}
    assert lines[2]["valid"] is False and "schema not supported" in lines[2]["error"]
    assert lines[2]["cost_usd"] == 0.0 and lines[2]["tokens_in"] == 0
    assert len(client.calls) == 3, "a bad request is not retried and does not stop the arm"
    assert len(live.usage) == 2 and live.sleeps == []


async def test_a_live_pdf_arm_skips_documents_without_a_pdf_and_sends_the_rest_as_document_blocks(
    live,
):
    async def pdfs(sample):
        ok = base64.standard_b64encode(b"%PDF-1.4 x").decode()
        return {1: (ok, None), 2: (None, "pdf unavailable"), 3: (ok, None)}

    live.monkeypatch.setattr(eb, "_fetch_pdfs", pdfs)
    client = FakeLive()

    await live.run_live("H-pdf-live", client)

    assert len(client.calls) == 2
    block = client.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"
    lines = {r["doc_id"]: r for r in _results(live.run_dir, "H-pdf-live")}
    assert lines[2]["error"] == "pdf unavailable" and lines[2]["cost_usd"] == 0.0
    assert lines[2]["mode"] == "live"
    assert len(live.usage) == 2


async def test_a_text_arm_skips_a_document_with_no_extracted_text(live):
    async def texts(doc_ids):
        return {1: "text one", 2: None, 3: "text three"}

    live.monkeypatch.setattr(eb, "_fetch_texts", texts)
    client = FakeLive()

    await live.run_live("H-text-live", client)

    lines = {r["doc_id"]: r for r in _results(live.run_dir, "H-text-live")}
    assert lines[2]["error"] == "no extracted text" and len(client.calls) == 2


async def test_the_sonnet_arms_send_only_the_subset_to_sonnet_with_the_same_params(live):
    client = FakeLive()

    out = await live.run_live("S-text", client)

    assert sorted(r["doc_id"] for r in _results(live.run_dir, "S-text")) == [1, 2]
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["max_tokens"] == 8192
    assert (
        call["output_config"]
        == eb.build_requests("H-text-live", [_fake_doc()])[0].params["output_config"]
    )
    assert "thinking" not in call and "temperature" not in call
    assert live.usage[0]["model"] == "claude-sonnet-5-5"
    first = _results(live.run_dir, "S-text")[0]
    assert first["cost_usd"] == pytest.approx(
        eb.pricing.cost_of("claude-sonnet-5-5", 1500, 220, batch=False)
    )
    assert out["S-text"].lines == 2


async def test_live_wall_time_is_recorded_for_the_report(live):
    await live.run_live("H-text-live", FakeLive())

    info = json.loads((live.run_dir / "H-text-live" / "live.json").read_text())
    assert info["arm"] == "H-text-live" and info["started_at"] and info["finished_at"]
    assert info["wall_seconds"] >= 0


async def test_the_cap_stops_a_live_arm_before_a_call_whose_worst_case_does_not_fit(live):
    [req] = eb.build_requests("S-text", [{**_fake_doc(1), "text": "Bulletin text for document 1."}])
    worst = eb.live_worst_case_usd(
        "S-text", req, {**_fake_doc(1), "text": "Bulletin text for document 1."}
    )
    actual = eb.pricing.cost_of("claude-sonnet-5-5", 1500, 220, batch=False)
    assert actual < worst
    # The first call fits; once it has been paid for, the second one's worst case cannot.
    live.monkeypatch.setattr(eb, "CAP_USD", worst + actual / 2)
    client = FakeLive()

    out = await live.run_live("S-text", client)

    assert len(client.calls) == 1
    assert out["S-text"].stopped_at_cap is True
    assert "stopped_at_cap" in eb.format_live_result(out["S-text"])
    assert eb.experiment_spend(live.run_dir) <= eb.CAP_USD


async def test_the_cap_counts_spend_already_in_the_bakeoff_and_sends_nothing_when_full(live):
    other = eb.BAKEOFF_ROOT / "20261101-0900" / "judge.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"cost_usd": eb.CAP_USD}) + "\n")
    client = FakeLive()

    out = await live.run_live("H-text-live", client)

    assert client.calls == [] and out["H-text-live"].stopped_at_cap is True
    assert live.usage == []


async def test_in_flight_calls_count_against_the_cap_when_several_run_at_once(live):
    [req] = eb.build_requests("S-text", [{**_fake_doc(1), "text": "Bulletin text for document 1."}])
    worst = eb.live_worst_case_usd(
        "S-text", req, {**_fake_doc(1), "text": "Bulletin text for document 1."}
    )
    actual = eb.pricing.cost_of("claude-sonnet-5-5", 1500, 220, batch=False)
    # Room for one call in flight, not two; and once the first is paid for, not a second.
    live.monkeypatch.setattr(eb, "CAP_USD", worst + actual / 2)
    client = FakeLive()

    out = await live.run_live("S-text", client, concurrency=2)

    assert len(client.calls) == 1 and out["S-text"].stopped_at_cap is True


async def test_a_live_arm_at_the_cap_stops_even_when_float_reservations_leave_a_residue(live):
    # 0.1 + 0.2 - 0.1 - 0.2 is 2.8e-17, not 0: a worker waiting for "nothing in flight"
    # by checking reserved == 0 would wait forever once both calls had settled.
    live.sonnet_path.write_text(json.dumps(live.entries[:3]))
    worst = {1: 0.1, 2: 0.2, 3: 1.0}
    live.monkeypatch.setattr(eb, "live_worst_case_usd", lambda arm, req, doc: worst[req.doc_id])
    live.monkeypatch.setattr(eb, "CAP_USD", 0.35)
    client = FakeLive()

    out = await asyncio.wait_for(live.run_live("S-text", client, concurrency=2), timeout=10)

    assert len(client.calls) == 2 and out["S-text"].stopped_at_cap is True


async def test_an_account_error_stops_the_live_arm_without_marking_documents_done(live):
    def no_credit_on_second(n, params):
        if n == 2:
            raise _http_error(
                anthropic.BadRequestError,
                400,
                "Your credit balance is too low to access the Anthropic API.",
            )

    with pytest.raises(eb.LiveCallFatal):
        await live.run_live("H-text-live", FakeLive(on_call=no_credit_on_second))
    assert [r["doc_id"] for r in _results(live.run_dir, "H-text-live")] == [1]

    client = FakeLive()
    await live.run_live("H-text-live", client)
    assert len(client.calls) == 2, "the documents the account error hit are sent again"
    assert sorted(r["doc_id"] for r in _results(live.run_dir, "H-text-live")) == [1, 2, 3]


async def test_a_bad_key_stops_the_live_arm_and_writes_no_result_lines(live):
    def unauthorized(n, params):
        raise _http_error(anthropic.AuthenticationError, 401, "invalid x-api-key")

    with pytest.raises(eb.LiveCallFatal):
        await live.run_live("H-text-live", FakeLive(on_call=unauthorized))

    path = live.run_dir / "H-text-live" / "results.jsonl"
    assert not path.exists() or _results(live.run_dir, "H-text-live") == []
    assert live.usage == [] and live.sleeps == []


async def test_run_live_all_live_runs_every_live_arm_in_order(live):
    client = FakeLive()

    out = await live.run_live("all-live", client)

    assert list(out) == ["H-text-live", "H-pdf-live", "S-text", "S-pdf"]
    assert len(client.calls) == 3 + 3 + 2 + 2


async def test_run_live_rejects_a_batch_arm_and_a_missing_key_before_reading_anything(live):
    with pytest.raises(ValueError, match="live arm"):
        await live.run_live("H-text", FakeLive())

    def no_key():
        raise RuntimeError("CLAUDE_API_KEY (or ANTHROPIC_API_KEY) is not set.")

    live.monkeypatch.setattr(eb, "_anthropic_client", no_key)
    live.monkeypatch.setattr(
        eb, "_fetch_texts", lambda ids: pytest.fail("a missing key must fail before the database")
    )
    with pytest.raises(RuntimeError, match="CLAUDE_API_KEY"):
        await eb.run_live(
            "H-text-live",
            live.run_dir,
            sample_path=live.sample_path,
            sonnet_sample_path=live.sonnet_path,
        )


def test_the_run_live_command_uses_the_newest_run_and_prints_one_summary_line_per_arm(live, capsys):
    client = FakeLive()
    live.use(anthropic_client=client)
    newest = eb.BAKEOFF_ROOT / "20261106-2117"
    newest.mkdir(parents=True)

    code = eb.main(
        [
            "run-live",
            "--arm",
            "S-text",
            "--concurrency",
            "1",
            "--sample",
            str(live.sample_path),
            "--sonnet-sample",
            str(live.sonnet_path),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0 and len(client.calls) == 2
    assert (newest / "S-text" / "results.jsonl").exists()
    [summary] = [line for line in out.splitlines() if line.startswith("S-text:")]
    for part in ("lines 2", "valid 2", "errors 0", "tokens 3000/440", "cost $", "wall"):
        assert part in summary


def test_status_lists_the_live_arms_with_a_list_price_estimate(live, capsys):
    entries = live.entries
    code = eb.main(
        [
            "status",
            "--run-dir",
            str(live.run_dir),
            "--sample",
            str(live.sample_path),
            "--sonnet-sample",
            str(live.sonnet_path),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    for arm in eb.LIVE_ARMS:
        assert arm in out
    sonnet_estimate = 2 * pricing_cost("claude-sonnet-5-5", 1000, 200) * 2
    haiku_estimate = 3 * pricing_cost("claude-haiku-5-5", 1000, 200) * 2
    assert eb.estimate_cost("H-text-live", entries) == pytest.approx(haiku_estimate)
    assert eb.estimate_cost("S-text", entries[:2]) == pytest.approx(sonnet_estimate)
    assert f"${sonnet_estimate:.4f}" in out and f"${haiku_estimate:.4f}" in out


# --------------------------------------------------------------------------- canceled batches


def _canceled(doc_id):
    return SimpleNamespace(custom_id=str(doc_id), result=SimpleNamespace(type="canceled"))


async def test_a_fully_canceled_batch_arm_collects_into_unbilled_canceled_lines(harness):
    client = FakeAnthropic(results=[_canceled(i) for i in (1, 2, 3)])
    harness.use(anthropic_client=client)
    await eb.submit("H-pdf", harness.run_dir, sample_path=harness.sample_path)

    report = await eb.collect(harness.run_dir)

    assert report["H-pdf"] == "collected 3 (0 valid)"
    lines = _results(harness.run_dir, "H-pdf")
    assert sorted(r["doc_id"] for r in lines) == [1, 2, 3]
    assert all("canceled" in r["error"] and r["valid"] is False for r in lines)
    assert all(r["cost_usd"] == 0.0 and r["tokens_in"] == 0 for r in lines)
    assert harness.usage == []
    spend = eb.run_spend(harness.run_dir)
    assert spend.collected == 0.0 and spend.committed == 0.0, "no longer committed once collected"


async def test_a_canceled_batch_with_some_results_that_ran_bills_those_once(harness):
    client = FakeAnthropic(
        results=[_succeeded(1, json.dumps(GOOD), tin=900, tout=100), _canceled(2), _canceled(3)]
    )
    harness.use(anthropic_client=client)
    await eb.submit("H-text", harness.run_dir, sample_path=harness.sample_path)

    await eb.collect(harness.run_dir)
    await eb.collect(harness.run_dir)

    lines = {r["doc_id"]: r for r in _results(harness.run_dir, "H-text")}
    assert lines[1]["valid"] is True and lines[1]["cost_usd"] > 0
    assert "canceled" in lines[2]["error"] and lines[3]["cost_usd"] == 0.0
    assert len(harness.usage) == 1 and harness.usage[0]["batch"] is True


# --------------------------------------------------------------------------- the three PDF arms

PDF_BYTES = b"%PDF-1.4 " + bytes(40)


def _gemini_error(cls, code, message="boom", status="UNAVAILABLE"):
    from google.genai import errors as genai_errors

    return getattr(genai_errors, cls)(code, {"error": {"message": message, "status": status}})


def _gemini_reply(text, tin=2000, tout=300, thoughts=0, finish="STOP"):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=tin, candidates_token_count=tout, thoughts_token_count=thoughts
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


class FakeGeminiLive:
    """Stands in for ``genai.Client`` on ``models.generate_content``."""

    def __init__(self, reply=None, on_call=None):
        self.calls = []
        self._reply = reply or (lambda params: _gemini_reply(json.dumps(GOOD)))
        self._on_call = on_call
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **params):
        self.calls.append(params)
        if self._on_call:
            self._on_call(len(self.calls), params)
        return self._reply(params)


async def _run_pdf_arm(live, arm, anthropic_client=None, gemini_client=None, **kw):
    live.use(anthropic_client=anthropic_client, gemini_client=gemini_client)
    return await eb.run_live(
        arm,
        live.run_dir,
        sample_path=live.sample_path,
        sonnet_sample_path=live.sonnet_path,
        concurrency=kw.pop("concurrency", 1),
        **kw,
    )


def test_a_gemini_pdf_arm_sends_the_pdf_inline_with_the_g_arm_schema_and_a_thinking_level():
    from google.genai import types as genai_types

    docs = [_pdf_doc(7, payload=PDF_BYTES)]

    [high] = eb.build_requests("GL-pdf-high", docs)
    [low] = eb.build_requests("GF-pdf-low", docs)

    part, prompt = high.params["contents"]
    assert part.inline_data.mime_type == "application/pdf" and part.inline_data.data == PDF_BYTES
    assert prompt == eb.PDF_PROMPT and llm._RULES in prompt
    assert high.params["model"] == "gemini-3.5-flash-lite"
    config = high.params["config"]
    assert config.thinking_config.thinking_level == genai_types.ThinkingLevel.HIGH
    assert config.temperature == 0 and config.response_mime_type == "application/json"
    assert config.response_schema == llm.RESPONSE_SCHEMA
    assert config.max_output_tokens == eb.GEMINI_MAX_OUTPUT_TOKENS
    assert low.params["model"] == "gemini-3.8-flash"
    assert low.params["config"].thinking_config.thinking_level == genai_types.ThinkingLevel.LOW
    assert low.params["contents"][1] == prompt
    assert eb.arm_input("GF-pdf-low") == "pdf" and eb.arm_mode("GF-pdf-low") == "live"


def test_a_pdf_too_big_to_send_inline_to_gemini_is_an_unbilled_error_row_not_a_request(monkeypatch):
    monkeypatch.setattr(eb, "GEMINI_PDF_MAX_BYTES", 20)

    [too_big] = eb.build_requests("GF-pdf-low", [_pdf_doc(7, payload=b"x" * 21)])
    [fits] = eb.build_requests("GF-pdf-low", [_pdf_doc(8, payload=b"x" * 20)])

    assert too_big.params is None and "pdf too large" in too_big.error
    assert fits.params is not None and fits.error is None


def test_the_haiku_high_arm_matches_the_live_pdf_arm_except_for_effort_thinking_and_ceiling():
    doc = _pdf_doc(7, payload=PDF_BYTES)

    [base] = eb.build_requests("H-pdf-live", [doc])
    [high] = eb.build_requests("H-pdf-high", [doc])

    assert high.params["output_config"]["effort"] == "high"
    assert high.params["thinking"] == {"type": "adaptive"}
    assert high.params["max_tokens"] == 16_000 > eb.HAIKU_MAX_TOKENS
    assert "temperature" not in high.params
    rest = {k: v for k, v in high.params.items() if k not in ("thinking", "max_tokens")}
    rest["output_config"] = {**rest["output_config"], "effort": "low"}
    assert rest == {k: v for k, v in base.params.items() if k != "max_tokens"}


def test_gemini_3_8_flash_is_priced_at_its_2026_rate_and_the_2027_rate_is_on_record():
    assert eb.pricing.cost_of("gemini-3.8-flash", 1_000_000, 1_000_000) == pytest.approx(
        0.75 + 3.75
    )
    assert eb.pricing.cost_of("gemini-3.8-flash", 2_000_000) != eb.pricing.cost_of(
        "gemini-3.5-flash-lite", 2_000_000
    ), "the flash-lite fallback rate must not price this model"
    year = eb.GEMINI_FLASH_RATE_2027
    assert (year.input_per_token, year.output_per_token) == (1.50 / 1e6, 7.50 / 1e6)


async def test_a_gemini_pdf_arm_writes_a_line_per_document_with_thinking_in_tokens_out(live):
    client = FakeGeminiLive(
        reply=lambda p: _gemini_reply(json.dumps(GOOD), 2500, 300, thoughts=900)
    )

    out = await _run_pdf_arm(live, "GF-pdf-low", gemini_client=client)

    assert len(client.calls) == 2, "only the 100-document subset is sent"
    lines = _results(live.run_dir, "GF-pdf-low")
    first = lines[0]
    assert first["model"] == "gemini-3.8-flash" and first["mode"] == "live"
    assert first["valid"] is True and first["error"] is None
    assert (first["tokens_in"], first["tokens_out"]) == (2500, 1200)
    assert first["cost_usd"] == pytest.approx(2500 * 0.75e-6 + 1200 * 3.75e-6)
    [ledger, _] = live.usage
    assert ledger["model"] == "gemini-3.8-flash" and ledger["batch"] is False
    assert ledger["meta"] == {
        "bakeoff": "GF-pdf-low",
        "run": str(live.run_dir),
        "doc_id": 1,
        "mode": "live",
    }
    assert ledger["tokens_out"] == 1200
    assert out["GF-pdf-low"].lines == 2 and out["GF-pdf-low"].valid == 2


async def test_a_gemini_pdf_arm_resumes_and_a_rerun_sends_nothing(live):
    await _run_pdf_arm(live, "GL-pdf-high", gemini_client=FakeGeminiLive())
    again = FakeGeminiLive()

    out = await _run_pdf_arm(live, "GL-pdf-high", gemini_client=again)

    assert again.calls == [] and out["GL-pdf-high"].sent == 0
    assert len(_results(live.run_dir, "GL-pdf-high")) == 2 and len(live.usage) == 2


async def test_a_gemini_response_cut_off_at_the_output_ceiling_is_an_error_but_still_billed(live):
    client = FakeGeminiLive(
        reply=lambda p: _gemini_reply(
            '{"summary": "cut off mid-se', 2000, 32000, finish="MAX_TOKENS"
        )
    )

    await _run_pdf_arm(live, "GL-pdf-high", gemini_client=client)

    [line, _] = _results(live.run_dir, "GL-pdf-high")
    assert line["valid"] is False and line["output"] is None
    assert "truncated" in line["error"] and "max_tokens" in line["error"]
    assert line["cost_usd"] > 0 and len(live.usage) == 2


async def test_a_haiku_response_cut_off_at_max_tokens_is_an_error_even_if_it_parses(live):
    client = FakeLive(
        reply=lambda p: _message(json.dumps(GOOD), tin=3000, tout=16000, stop="max_tokens")
    )

    await _run_pdf_arm(live, "H-pdf-high", anthropic_client=client)

    [line, _] = _results(live.run_dir, "H-pdf-high")
    assert line["valid"] is False and line["output"] is None
    assert line["error"].startswith("truncated (stop_reason=max_tokens)")
    assert line["cost_usd"] > 0, "a truncated response was still billed"
    call = client.calls[0]
    assert call["output_config"]["effort"] == "high" and call["thinking"] == {"type": "adaptive"}
    assert call["max_tokens"] == 16_000
    assert call["messages"][0]["content"][0]["type"] == "document"


async def test_a_gemini_server_error_is_retried_and_a_bad_request_is_an_unbilled_error_line(live):
    def flaky(n, params):
        if n == 1:
            raise _gemini_error("ServerError", 503)
        if n == 3:
            raise _gemini_error("ClientError", 400, "bad schema", "INVALID_ARGUMENT")

    client = FakeGeminiLive(on_call=flaky)

    await _run_pdf_arm(live, "GL-pdf-high", gemini_client=client)

    lines = {r["doc_id"]: r for r in _results(live.run_dir, "GL-pdf-high")}
    assert lines[1]["valid"] is True and len(client.calls) == 3
    assert lines[2]["valid"] is False and "bad schema" in lines[2]["error"]
    assert lines[2]["cost_usd"] == 0.0 and len(live.usage) == 1
    assert live.sleeps == [2.0]


async def test_a_gemini_account_error_stops_the_arm_and_writes_no_result_line(live):
    def bad_key(n, params):
        raise _gemini_error(
            "ClientError",
            400,
            "API key not valid. Please pass a valid API key.",
            "INVALID_ARGUMENT",
        )

    with pytest.raises(eb.LiveCallFatal):
        await _run_pdf_arm(live, "GF-pdf-low", gemini_client=FakeGeminiLive(on_call=bad_key))

    path = live.run_dir / "GF-pdf-low" / "results.jsonl"
    assert not path.exists() or _results(live.run_dir, "GF-pdf-low") == []
    assert live.usage == [] and live.sleeps == []


async def test_a_gemini_pdf_arm_skips_a_document_with_no_pdf_without_calling(live):
    async def pdfs(sample):
        ok = base64.standard_b64encode(PDF_BYTES).decode()
        return {1: (ok, None), 2: (None, "pdf unavailable")}

    live.monkeypatch.setattr(eb, "_fetch_pdfs", pdfs)
    client = FakeGeminiLive()

    await _run_pdf_arm(live, "GF-pdf-low", gemini_client=client)

    assert len(client.calls) == 1
    lines = {r["doc_id"]: r for r in _results(live.run_dir, "GF-pdf-low")}
    assert lines[2]["error"] == "pdf unavailable" and lines[2]["cost_usd"] == 0.0


async def test_pdf3_runs_the_three_arms_in_order_with_the_right_client_for_each(live):
    anthropic_client, gemini_client = FakeLive(), FakeGeminiLive()

    out = await _run_pdf_arm(live, "pdf3", anthropic_client, gemini_client)

    assert list(out) == ["GL-pdf-high", "GF-pdf-low", "H-pdf-high"]
    assert [c["model"] for c in gemini_client.calls] == ["gemini-3.5-flash-lite"] * 2 + [
        "gemini-3.8-flash"
    ] * 2
    assert len(anthropic_client.calls) == 2


def test_the_run_live_command_accepts_the_new_arms_and_pdf3(live, capsys):
    gemini_client = FakeGeminiLive()
    live.use(anthropic_client=FakeLive(), gemini_client=gemini_client)
    live.run_dir.mkdir(parents=True)
    argv = [
        "run-live",
        "--run-dir",
        str(live.run_dir),
        "--concurrency",
        "1",
        "--sample",
        str(live.sample_path),
        "--sonnet-sample",
        str(live.sonnet_path),
    ]

    assert eb.main([*argv, "--arm", "pdf3"]) == 0

    out = capsys.readouterr().out
    for arm in eb.PDF3_ARMS:
        assert f"{arm}: lines 2 valid 2" in out
    with pytest.raises(SystemExit):
        eb.main([*argv, "--arm", "nonsense"])


async def test_all_live_still_runs_only_the_original_four_arms(live):
    out = await _run_pdf_arm(live, "all-live", FakeLive(), FakeGeminiLive())

    assert list(out) == list(eb.LIVE_ARMS)


# ---- the effort arms above high (H-pdf-xhigh, H-pdf-max)


class FakeStreamLive(FakeLive):
    """``FakeLive`` that also has ``messages.stream``; the call must go through it."""

    def __init__(self, reply=None, on_call=None):
        super().__init__(reply, on_call)
        self.streamed = 0

        def _no_create(**_):
            raise AssertionError("a streaming arm must not call messages.create")

        self.messages = SimpleNamespace(create=_no_create, stream=self._stream)

    def _stream(self, **params):
        self.calls.append(params)
        self.streamed += 1
        if self._on_call:
            self._on_call(len(self.calls), params)
        message = self._reply(params)

        class _Ctx:
            def __enter__(_self):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(_self, *exc):
                return False

        return _Ctx()


@pytest.mark.parametrize("arm,effort", [("H-pdf-xhigh", "xhigh"), ("H-pdf-max", "max")])
def test_an_effort_arm_matches_the_high_arm_except_for_the_effort_level(arm, effort):
    doc = _pdf_doc(7, payload=PDF_BYTES)

    [high] = eb.build_requests("H-pdf-high", [doc])
    [req] = eb.build_requests(arm, [doc])

    assert req.params["output_config"]["effort"] == effort
    assert req.params["thinking"] == {"type": "adaptive"}
    assert req.params["max_tokens"] == 64_000 == eb.HAIKU_EFFORT_MAX_TOKENS
    assert "temperature" not in req.params
    same = lambda p: {  # noqa: E731
        k: (v if k != "output_config" else {**v, "effort": "-"})
        for k, v in p.items()
        if k != "max_tokens"
    }
    assert same(req.params) == same(high.params)
    assert eb.arm_mode(arm) == "live" and eb.arm_input(arm) == "pdf"
    assert arm in eb.SUBSET_ARMS and arm in eb.ALL_ARMS and arm not in eb.DEFAULT_ARMS


async def test_an_effort_arm_streams_counts_thinking_in_tokens_out_and_records_latency(live):
    client = FakeStreamLive(reply=lambda p: _message(json.dumps(GOOD), tin=3000, tout=9000))

    await _run_pdf_arm(live, "H-pdf-xhigh", anthropic_client=client)

    assert client.streamed == 2
    [line, _] = _results(live.run_dir, "H-pdf-xhigh")
    assert line["valid"] is True and line["tokens_out"] == 9000
    assert line["cost_usd"] == pytest.approx(eb.pricing.cost_of(eb.HAIKU_MODEL, 3000, 9000))
    assert line["latency_s"] >= 0
    call = client.calls[0]
    assert call["output_config"]["effort"] == "xhigh" and call["max_tokens"] == 64_000
    assert call["messages"][0]["content"][0]["type"] == "document"


async def test_a_streamed_response_cut_off_at_max_tokens_is_recorded_as_a_truncation_error(live):
    client = FakeStreamLive(
        reply=lambda p: _message(json.dumps(GOOD), tin=3000, tout=64000, stop="max_tokens")
    )

    await _run_pdf_arm(live, "H-pdf-max", anthropic_client=client)

    [line, _] = _results(live.run_dir, "H-pdf-max")
    assert line["valid"] is False and line["output"] is None
    assert line["error"].startswith("truncated (stop_reason=max_tokens)")
    assert line["tokens_out"] == 64000 and line["cost_usd"] > 0


async def test_haiku_effort_runs_both_arms_in_order(live):
    client = FakeStreamLive()

    out = await _run_pdf_arm(live, "haiku-effort", anthropic_client=client)

    assert list(out) == ["H-pdf-xhigh", "H-pdf-max"]
    assert [c["output_config"]["effort"] for c in client.calls] == ["xhigh"] * 2 + ["max"] * 2


def test_the_run_live_command_accepts_an_effort_arm(live, capsys):
    live.use(anthropic_client=FakeStreamLive())
    live.run_dir.mkdir(parents=True)
    argv = ["run-live", "--run-dir", str(live.run_dir), "--concurrency", "1"]
    argv += ["--sample", str(live.sample_path), "--sonnet-sample", str(live.sonnet_path)]

    assert eb.main([*argv, "--arm", "H-pdf-max"]) == 0

    assert "H-pdf-max: lines 2 valid 2" in capsys.readouterr().out


def test_the_worst_case_of_a_64k_effort_call_counts_every_output_token():
    [req] = eb.build_requests("H-pdf-max", [_pdf_doc(7, payload=PDF_BYTES)])

    worst = eb.live_worst_case_usd("H-pdf-max", req, {"page_count": 10})

    tin = 10 * eb.PDF_TOKENS_PER_PAGE + len(eb.PDF_PROMPT) // 2
    assert worst == pytest.approx(eb.pricing.cost_of(eb.HAIKU_MODEL, tin, 64_000))


# ---- the cap counts every dollar of the bake-off


def _write_pairwise(run_dir, costs):
    folder = run_dir / "pairwise-G-vs-Hpdf"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "verdicts.jsonl").write_text(
        "".join(json.dumps({"doc_id": i, "cost_usd": c}) + "\n" for i, c in enumerate(costs))
    )


def _write_probe(run_dir, variants):
    folder = run_dir / "probe-2027"
    folder.mkdir(parents=True, exist_ok=True)
    body = {
        name: {"cost_true": true, "cost_ledger": ledger}
        for name, (true, ledger) in variants.items()
    }
    body["_meta"] = {"spend_before": 99.0}
    (folder / "results.json").write_text(json.dumps(body))


def test_the_cap_counts_the_pairwise_judge_and_the_probe_not_just_the_arms_and_the_judge(tmp_path):
    run = eb.BAKEOFF_ROOT / "20261106-2117"
    _write_pairwise(run, [1.5, 2.25])
    _write_probe(run, {"A": (0.0764, 0.0153), "B": (0.0078, 0.0078)})

    assert eb.side_spend(run) == pytest.approx(3.75 + 0.0764 + 0.0078)
    assert eb.experiment_spend(run) == pytest.approx(3.75 + 0.0764 + 0.0078)


def test_every_judge_subdirectory_counts_so_the_six_way_judge_cannot_slip_past_the_cap():
    run = eb.BAKEOFF_ROOT / "20261106-2117"
    (run / "judge-pdf6").mkdir(parents=True)
    (run / "judge-pdf6" / "judge.jsonl").write_text(json.dumps({"cost_usd": 2.0}) + "\n")
    (run / "judge.jsonl").write_text(json.dumps({"cost_usd": 0.5}) + "\n")

    assert eb.judge_spend(run) == pytest.approx(2.5)


async def test_a_live_arm_refuses_to_send_when_pairwise_and_probe_spend_leave_no_room(live):
    other = eb.BAKEOFF_ROOT / "20261106-2117"
    _write_pairwise(other, [eb.CAP_USD - 0.05])
    _write_probe(other, {"A": (0.04, 0.01)})
    client = FakeGeminiLive()

    out = await _run_pdf_arm(live, "GF-pdf-low", gemini_client=client)

    assert client.calls == [] and out["GF-pdf-low"].stopped_at_cap is True
    assert live.usage == []


def test_the_cap_is_thirty_dollars():
    assert eb.CAP_USD == 30.0
