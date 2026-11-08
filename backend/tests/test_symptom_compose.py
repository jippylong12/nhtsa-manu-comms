"""Grounded answer composition: confidence gate, citation guard, refusal paths.

The LLM call is isolated in ``compose._generate``; these tests monkeypatch it so
the guard and no-match logic are exercised deterministically without the network.
"""

import pytest

from src.symptom_search import compose as compose_mod
from src.symptom_search.compose import (
    CONFIDENCE_THRESHOLD,
    MAX_OUTPUT_TOKENS,
    REFUSAL_TEXT,
    SUMMARY_CHAR_CAP,
    TOP_K_FOR_ANSWER,
    ComposeError,
    ComposeResult,
    compose,
    guard_citations,
)
from src.symptom_search.cost import QueryCost
from src.symptom_search.retrieval import Match

# asyncio_mode=auto (pytest.ini) runs the async tests; sync guard tests stay sync.


def _match(nhtsa_id: str, cosine: float, summary: str = "A relevant bulletin.") -> Match:
    return Match(
        nhtsa_id=nhtsa_id,
        communication_number=f"NUM-{nhtsa_id}",
        communication_type="TSB",
        communication_date=None,
        llm_summary=summary,
        symptoms=["symptom"],
        systems=["electrical"],
        components=["module"],
        remedy="reprogram",
        doc_kind="service_procedure",
        score=0.03,
        cosine_sim=cosine,
        vector_rank=1,
        fts_rank=1,
        matched_systems=["electrical"],
    )


def _fake_generate(payload_json: str):
    def _gen(model: str, prompt: str):
        return payload_json, 100, 40

    return _gen


# --------------------------------------------------------------------------- #
# guard_citations (pure)
# --------------------------------------------------------------------------- #
def test_guard_keeps_valid_and_strips_hallucinated():
    answer = "The screen issue matches [11018655]. Also see [99999999] for details."
    clean, valid, hallucinated = guard_citations(answer, {"11018655"})
    assert valid == ["11018655"]
    assert hallucinated == ["99999999"]
    assert "99999999" not in clean
    assert "11018655" in clean


def test_guard_dedupes_repeated_citations():
    answer = "See [11018655] and again [11018655]."
    _, valid, _ = guard_citations(answer, {"11018655"})
    assert valid == ["11018655"]


def test_guard_no_citations():
    clean, valid, hallucinated = guard_citations("No ids here.", {"111111"})
    assert valid == [] and hallucinated == []
    assert clean == "No ids here."


# --------------------------------------------------------------------------- #
# Confidence gate (no LLM call)
# --------------------------------------------------------------------------- #
async def test_low_confidence_refuses_without_calling_llm(monkeypatch):
    def _boom(model, prompt):
        raise AssertionError("LLM must not be called below the confidence threshold")

    monkeypatch.setattr(compose_mod, "_generate", _boom)
    matches = [_match("10000001", cosine=CONFIDENCE_THRESHOLD - 0.1)]
    cost = QueryCost()
    result = await compose("some unrelated thing", matches, cost)
    assert result.refused is True
    assert result.answer == REFUSAL_TEXT
    assert cost.llm_cost_usd == 0.0  # no LLM spend on a gated refusal


async def test_empty_matches_refuses():
    result = await compose("anything", [], QueryCost())
    assert result.refused is True


# --------------------------------------------------------------------------- #
# Citation guard through compose()
# --------------------------------------------------------------------------- #
async def test_hallucinated_citation_is_stripped(monkeypatch):
    payload = (
        '{"answer": "This matches [10000001], and also [88888888].", '
        '"relevant": true, "cited_ids": ["10000001", "88888888"]}'
    )
    monkeypatch.setattr(compose_mod, "_generate", _fake_generate(payload))
    matches = [_match("10000001", cosine=0.80)]
    cost = QueryCost()
    result = await compose("real symptom", matches, cost)
    assert result.refused is False
    assert result.cited_ids == ["10000001"]
    assert "88888888" not in result.answer
    assert result.hallucinated_stripped == ["88888888"]
    assert cost.llm_cost_usd > 0  # the call happened and was costed


async def test_relevant_but_only_hallucinated_citations_downgrades_to_refusal(monkeypatch):
    payload = (
        '{"answer": "Definitely see [77777777].", "relevant": true, ' '"cited_ids": ["77777777"]}'
    )
    monkeypatch.setattr(compose_mod, "_generate", _fake_generate(payload))
    matches = [_match("10000001", cosine=0.80)]
    result = await compose("real symptom", matches, QueryCost())
    assert result.refused is True
    assert result.answer == REFUSAL_TEXT


async def test_model_says_not_relevant_refuses(monkeypatch):
    payload = '{"answer": "Nothing here matches.", "relevant": false, "cited_ids": []}'
    monkeypatch.setattr(compose_mod, "_generate", _fake_generate(payload))
    matches = [_match("10000001", cosine=0.80)]
    result = await compose("real symptom", matches, QueryCost())
    assert result.refused is True


async def test_grounded_answer_passes_through(monkeypatch):
    payload = (
        '{"answer": "Your no-heat high-voltage warning matches [10000001].", '
        '"relevant": true, "cited_ids": ["10000001"]}'
    )
    monkeypatch.setattr(compose_mod, "_generate", _fake_generate(payload))
    matches = [_match("10000001", cosine=0.79)]
    result = await compose("service high voltage and no heat", matches, QueryCost())
    assert result.refused is False
    assert result.cited_ids == ["10000001"]
    assert "[10000001]" in result.answer


def test_compose_result_defaults():
    r = ComposeResult(answer="x")
    assert r.cited_ids == [] and r.refused is False


# --------------------------------------------------------------------------- #
# The tight prompt: top 4, 300-char summaries, one sentence (the $0.10/1k budget)
# --------------------------------------------------------------------------- #


async def test_only_the_top_four_candidates_reach_the_prompt(monkeypatch):
    seen: dict[str, str] = {}

    def _gen(model: str, prompt: str):
        seen["prompt"] = prompt
        return '{"answer": "Yes [100001].", "relevant": true, "cited_ids": ["100001"]}', 100, 40

    monkeypatch.setattr(compose_mod, "_generate", _gen)
    matches = [_match(str(100001 + i), 0.9 - i * 0.01) for i in range(6)]
    await compose("rattle", matches, QueryCost())

    assert TOP_K_FOR_ANSWER == 4
    for m in matches[:4]:
        assert f"[{m.nhtsa_id}]" in seen["prompt"]
    for m in matches[4:]:
        assert f"[{m.nhtsa_id}]" not in seen["prompt"]


async def test_summaries_are_capped_at_300_chars(monkeypatch):
    seen: dict[str, str] = {}

    def _gen(model: str, prompt: str):
        seen["prompt"] = prompt
        return '{"answer": "Yes [100001].", "relevant": true, "cited_ids": ["100001"]}', 100, 40

    monkeypatch.setattr(compose_mod, "_generate", _gen)
    long_summary = "x" * 1000
    await compose("rattle", [_match("100001", 0.9, summary=long_summary)], QueryCost())

    assert SUMMARY_CHAR_CAP == 300
    assert "x" * 300 in seen["prompt"]
    assert "x" * 301 not in seen["prompt"]


def test_prompt_asks_for_one_sentence():
    assert "ONE" in compose_mod._PROMPT and "sentence" in compose_mod._PROMPT
    assert "2-4 sentence" not in compose_mod._PROMPT
    assert (
        "One plain-language sentence"
        in compose_mod._RESPONSE_SCHEMA["properties"]["answer"]["description"]
    )


# --------------------------------------------------------------------------- #
# _generate: the Claude request shape, without the network
# --------------------------------------------------------------------------- #


class _Block:
    def __init__(self, type_: str, text: str = ""):
        self.type = type_
        self.text = text


class _Usage:
    input_tokens = 123
    output_tokens = 45


class _Message:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [_Block("text", text)]
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self, message: _Message):
        self.message = message
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.message


class _FakeClient:
    def __init__(self, message: _Message):
        self.messages = _FakeMessages(message)
        self.options: dict = {}

    def with_options(self, **options):
        self.options = options
        return self


def test_generate_calls_claude_haiku_with_thinking_off_and_json_schema(monkeypatch):
    fake = _FakeClient(_Message('{"answer": "a", "relevant": false, "cited_ids": []}'))
    monkeypatch.setattr(compose_mod, "anthropic_client", lambda: fake)

    text, tokens_in, tokens_out = compose_mod._generate("claude-haiku-5-5", "PROMPT")

    assert text == '{"answer": "a", "relevant": false, "cited_ids": []}'
    assert (tokens_in, tokens_out) == (123, 45)
    kw = fake.messages.kwargs
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["max_tokens"] == MAX_OUTPUT_TOKENS
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"] is compose_mod._RESPONSE_SCHEMA
    assert kw["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert "temperature" not in kw


def test_generate_raises_when_cut_off_at_max_tokens_and_carries_the_billed_tokens(monkeypatch):
    fake = _FakeClient(_Message('{"answer": "a', stop_reason="max_tokens"))
    monkeypatch.setattr(compose_mod, "anthropic_client", lambda: fake)
    with pytest.raises(ComposeError, match="max_tokens") as exc:
        compose_mod._generate("claude-haiku-5-5", "PROMPT")
    # Anthropic billed these even though the answer is unusable.
    assert (exc.value.tokens_in, exc.value.tokens_out) == (123, 45)


def test_generate_raises_on_refusal_and_carries_the_billed_tokens(monkeypatch):
    fake = _FakeClient(_Message("", stop_reason="refusal"))
    monkeypatch.setattr(compose_mod, "anthropic_client", lambda: fake)
    with pytest.raises(ComposeError, match="refused") as exc:
        compose_mod._generate("claude-haiku-5-5", "PROMPT")
    assert (exc.value.tokens_in, exc.value.tokens_out) == (123, 45)


async def test_a_billed_call_that_fails_still_records_its_cost(monkeypatch):
    """A cut-off or refused call is paid for; compose must put it on the cost
    before raising, or service.py writes no ledger row for it."""

    def _cut_off(model: str, prompt: str):
        raise ComposeError("composition hit max_tokens", tokens_in=700, tokens_out=200)

    monkeypatch.setattr(compose_mod, "_generate", _cut_off)
    cost = QueryCost()
    with pytest.raises(ComposeError):
        await compose("rattle", [_match("100001", 0.9)], cost)
    assert (cost.llm_tokens_in, cost.llm_tokens_out) == (700, 200)
    assert cost.llm_model == compose_mod.get_settings().compose_model
    assert cost.llm_cost_usd > 0


async def test_an_unbilled_failure_records_nothing(monkeypatch):
    def _no_key(model: str, prompt: str):
        raise ComposeError("CLAUDE_API_KEY is not set")

    monkeypatch.setattr(compose_mod, "_generate", _no_key)
    cost = QueryCost()
    with pytest.raises(ComposeError):
        await compose("rattle", [_match("100001", 0.9)], cost)
    assert (cost.llm_tokens_in, cost.llm_tokens_out, cost.llm_cost_usd) == (0, 0, 0.0)


def test_generate_uses_a_short_compose_timeout_with_one_retry(monkeypatch):
    """The shared client is sized for the extraction job (300s, two retries). A
    user-facing search must not inherit that: one stalled call would hold the
    request for minutes."""
    fake = _FakeClient(_Message('{"answer": "a", "relevant": false, "cited_ids": []}'))
    monkeypatch.setattr(compose_mod, "anthropic_client", lambda: fake)
    settings = compose_mod.get_settings()

    compose_mod._generate("claude-haiku-5-5", "PROMPT")

    assert fake.options["timeout"] == float(settings.compose_timeout_seconds)
    assert fake.options["timeout"] < settings.anthropic_timeout_seconds
    assert fake.options["max_retries"] == 1


def test_generate_raises_compose_error_when_key_is_missing(monkeypatch):
    def _no_key():
        raise RuntimeError("CLAUDE_API_KEY (or ANTHROPIC_API_KEY) is not set.")

    monkeypatch.setattr(compose_mod, "anthropic_client", _no_key)
    with pytest.raises(ComposeError, match="CLAUDE_API_KEY"):
        compose_mod._generate("claude-haiku-5-5", "PROMPT")
