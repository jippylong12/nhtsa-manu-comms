"""Grounded answer composition for symptom search.

One LLM call over the top-k retrieved communications composes a short answer
that cites nhtsa_ids inline. Two guards bracket the call:

  * A retrieval-confidence gate (before the call). If the best match's cosine
    similarity is below CONFIDENCE_THRESHOLD, a refusal is returned without
    spending an LLM call. The threshold comes from the B1 eval data: matching
    golden queries scored top-1 cosine >= 0.668, deliberately unrelated queries
    <= 0.624, so 0.65 separates them with margin.

  * A citation guard (after the call). Every nhtsa_id the model cites must be in
    the retrieved set. A fabricated id is stripped from the answer text and the
    cited list. If the model claimed a match but nothing valid survives, the
    answer is downgraded to a refusal rather than shipped ungrounded.

The single LLM call is isolated in ``_generate`` so tests can drive the guard
and no-match paths without hitting the network.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from src.config import get_settings
from src.symptom_search.cost import QueryCost
from src.symptom_search.retrieval import Match

log = logging.getLogger("symptom_search.compose")

# From the B1 eval data (see module docstring). Re-validated in JIP-381.
CONFIDENCE_THRESHOLD = 0.65
# How many retrieved communications are handed to the composer.
TOP_K_FOR_ANSWER = 5
# Keep each document's summary bounded so the prompt stays small and cheap.
SUMMARY_CHAR_CAP = 600

REFUSAL_TEXT = (
    "I could not find any manufacturer communications for this vehicle that match "
    "the problem you described. Try rephrasing with different wording, adding more "
    "detail about when it happens, or checking a different vehicle."
)

# nhtsa_ids in this corpus are 6+ digit numbers; the composer is told to cite as
# [nhtsa_id], and this pulls those bracketed citations back out for the guard.
_CITATION_RE = re.compile(r"\[\s*([0-9]{6,})\s*\]")

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": (
                "2-4 sentence plain-language answer. Reference each relevant "
                "communication inline as [nhtsa_id]. If none are relevant, say so."
            ),
        },
        "relevant": {
            "type": "boolean",
            "description": "True only if at least one candidate actually addresses the problem.",
        },
        "cited_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The nhtsa_ids referenced in the answer.",
        },
    },
    "required": ["answer", "relevant", "cited_ids"],
}

_PROMPT = """You help a vehicle owner find out whether any manufacturer communications \
(technical service bulletins, recalls, service updates) match a problem they describe. \
You are given the CANDIDATE COMMUNICATIONS that were retrieved for their specific vehicle.

Rules:
- Use ONLY the candidate communications listed below. Never mention a communication, \
NHTSA ID, or fix that is not in the list.
- Cite every communication you reference inline as [nhtsa_id], using the exact id shown \
in brackets in the list.
- If one or more candidates clearly address the described problem, give a short \
(2-4 sentence) answer that names what matches and cites the id(s), and set relevant=true.
- If NONE of the candidates actually address the described problem, set relevant=false \
and say plainly that you found no matching communication. Do not stretch an unrelated \
bulletin to fit.
- Be honest about uncertainty. It is better to report no match than to invent one.

DESCRIBED PROBLEM:
{query}

CANDIDATE COMMUNICATIONS:
{context}
"""


@dataclass
class ComposeResult:
    answer: str
    cited_ids: list[str] = field(default_factory=list)
    refused: bool = False
    confidence: float = 0.0
    model: str = ""
    hallucinated_stripped: list[str] = field(default_factory=list)


class ComposeError(RuntimeError):
    """The LLM composition call failed; retrieval still succeeded."""


def _format_context(matches: list[Match]) -> str:
    blocks = []
    for m in matches:
        summary = (m.llm_summary or "").strip()[:SUMMARY_CHAR_CAP]
        parts = [
            f"[{m.nhtsa_id}] {m.communication_type or ''} {m.communication_number or ''}".strip()
        ]
        if summary:
            parts.append(f"Summary: {summary}")
        if m.symptoms:
            parts.append("Symptoms: " + ", ".join(m.symptoms))
        if m.systems:
            parts.append("Systems: " + ", ".join(m.systems))
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def guard_citations(answer: str, retrieved_ids: set[str]) -> tuple[str, list[str], list[str]]:
    """Strip fabricated citations from the answer.

    Returns ``(clean_answer, valid_cited, hallucinated)``. A citation is valid
    only if its nhtsa_id is in the retrieved set; anything else is removed from
    the text so a hallucinated id can never reach the user.
    """
    cited = _CITATION_RE.findall(answer)
    valid: list[str] = []
    hallucinated: list[str] = []
    seen: set[str] = set()
    for cid in cited:
        target = valid if cid in retrieved_ids else hallucinated
        if cid not in seen:
            target.append(cid)
            seen.add(cid)

    clean = answer
    for bad in hallucinated:
        # Remove the bracketed citation itself; collapse any doubled spaces left.
        clean = re.sub(r"\[\s*" + re.escape(bad) + r"\s*\]", "", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, valid, hallucinated


def _generate(model: str, prompt: str) -> tuple[str, int, int]:
    """Run the composition call. Isolated so tests can monkeypatch it."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ComposeError("GEMINI_API_KEY is not set. Add it to backend/.env.")
    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0.0,
        ),
    )
    usage = resp.usage_metadata
    tokens_in = (usage.prompt_token_count or 0) if usage else 0
    tokens_out = (usage.candidates_token_count or 0) if usage else 0
    return resp.text or "", tokens_in, tokens_out


async def compose(
    query: str,
    matches: list[Match],
    cost: QueryCost,
    *,
    model: str | None = None,
) -> ComposeResult:
    """Compose a grounded answer over the retrieved matches.

    ``cost`` accumulates the composition tokens/cost. Raises ``ComposeError``
    if the LLM call itself fails (retrieval has already succeeded by then).
    """
    model = model or get_settings().gemini_model

    # Gate on the same top-k the composer actually reasons over. The 0.65
    # threshold was derived from top-k cosine in the B1 eval, so measuring it
    # over these exact candidates keeps the gate consistent with its calibration.
    candidates = matches[:TOP_K_FOR_ANSWER]
    confidence = max((m.cosine_sim or 0.0) for m in candidates) if candidates else 0.0

    # Confidence gate: refuse cheaply, before any LLM spend.
    if not candidates or confidence < CONFIDENCE_THRESHOLD:
        log.info(
            "refusing (confidence %.3f < %.2f) without LLM call", confidence, CONFIDENCE_THRESHOLD
        )
        return ComposeResult(answer=REFUSAL_TEXT, refused=True, confidence=confidence, model=model)

    prompt = _PROMPT.format(query=query, context=_format_context(candidates))

    try:
        raw, tokens_in, tokens_out = await asyncio.to_thread(_generate, model, prompt)
    except ComposeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ComposeError(f"{type(e).__name__}: {e}") from e

    cost.record_llm(model, tokens_in, tokens_out)

    try:
        payload = json.loads(raw)
        answer = str(payload.get("answer", "")).strip()
        model_relevant = bool(payload.get("relevant", False))
    except (json.JSONDecodeError, TypeError) as e:
        raise ComposeError(f"composition returned invalid JSON: {e}") from e

    retrieved_ids = {m.nhtsa_id for m in candidates}
    clean, valid_cited, hallucinated = guard_citations(answer, retrieved_ids)
    if hallucinated:
        log.warning("stripped hallucinated citations: %s", hallucinated)

    # Downgrade to a refusal when the model claimed a match but nothing grounded
    # survives the guard: an answer citing no retrieved document is not grounded.
    if not model_relevant or not valid_cited:
        return ComposeResult(
            answer=REFUSAL_TEXT,
            refused=True,
            confidence=confidence,
            model=model,
            hallucinated_stripped=hallucinated,
        )

    return ComposeResult(
        answer=clean,
        cited_ids=valid_cited,
        refused=False,
        confidence=confidence,
        model=model,
        hallucinated_stripped=hallucinated,
    )
