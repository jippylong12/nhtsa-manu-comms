"""Grounded answer composition: confidence gate, citation guard, refusal paths.

The LLM call is isolated in ``compose._generate``; these tests monkeypatch it so
the guard and no-match logic are exercised deterministically without the network.
"""

from src.symptom_search import compose as compose_mod
from src.symptom_search.compose import (
    CONFIDENCE_THRESHOLD,
    REFUSAL_TEXT,
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
