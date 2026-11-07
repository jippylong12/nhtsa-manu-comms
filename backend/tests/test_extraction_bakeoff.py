"""The extraction bake-off harness (JIP-1567).

Every test here is offline. The provider SDK clients and the HTTP layer are
replaced with fakes, and an autouse guard fails the test if the real thing is
ever constructed, so no test can spend money or open a connection.
"""

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
    (other / "results.jsonl").write_text(json.dumps({"cost_usd": 9.99}) + "\n")
    (eb.BAKEOFF_ROOT / "20261101-0900" / "judge.jsonl").write_text(
        json.dumps({"cost_usd": 0.5}) + "\n"
    )
    gemini = FakeGemini()
    harness.use(gemini_client=gemini)

    assert eb.experiment_spend(harness.run_dir) == pytest.approx(10.49)
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
