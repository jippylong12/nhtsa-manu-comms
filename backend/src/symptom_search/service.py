"""Symptom-search orchestration: embed once, retrieve, compose.

Ties the B1 retrieval to the B2 grounded composer and accumulates the whole
query's cost (one embedding + at most one LLM call). The query is embedded here
and the vector is handed to retrieval so it is never embedded twice.
"""

import logging
from typing import Optional

from src.db import get_pool
from src.jobs.embed import embed_query
from src.symptom_search.compose import ComposeError, compose
from src.symptom_search.cost import QueryCost
from src.symptom_search.retrieval import retrieve

log = logging.getLogger("symptom_search.service")


async def vehicle_exists(vehicle_id: int) -> bool:
    """Whether a vehicle with this NHTSA vehicleId is in the corpus."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM vehicles WHERE nhtsa_vehicle_id = $1)",
                vehicle_id,
            )
        )


# Communications returned to the caller when a match is found. The composer only
# reads the top few; the rest give the UI supporting context.
RETRIEVE_K = 6

_COMPOSE_FALLBACK = (
    "Possible matches are listed below, but an automatic summary could not be "
    "generated for this query. Review the communications directly."
)


async def search(
    vehicle_id: int,
    description: str,
    *,
    model: Optional[str] = None,
) -> dict:
    """Run a symptom query end to end and return the API payload as a dict."""
    description = (description or "").strip()
    cost = QueryCost()

    # Embed once, record its cost, and reuse the vector for retrieval.
    embedding = await embed_query(description)
    cost.record_embed(description)

    result = await retrieve(vehicle_id, description, k=RETRIEVE_K, embedding=embedding, cost=cost)

    answer_generated = True
    try:
        composed = await compose(description, result.matches, cost, model=model)
        answer = composed.answer
        refused = composed.refused
        cited_ids = composed.cited_ids
        confidence = composed.confidence
        used_model = composed.model
    except ComposeError as e:
        # Retrieval already succeeded; degrade to showing matches without a
        # composed answer rather than failing the whole request.
        log.error("composition failed: %s", e)
        answer = _COMPOSE_FALLBACK
        refused = False
        cited_ids = []
        confidence = max((m.cosine_sim or 0.0) for m in result.matches) if result.matches else 0.0
        used_model = model or ""
        answer_generated = False

    # A refusal is a first-class no-match answer: do not show weak matches
    # alongside it, or the honest "nothing matched" reads as "here are matches".
    visible = [] if refused else result.matches

    matches_out = [
        {
            "nhtsa_id": m.nhtsa_id,
            "communication_number": m.communication_number,
            "communication_type": m.communication_type,
            "communication_date": m.communication_date,
            "llm_summary": m.llm_summary,
            "symptoms": m.symptoms,
            "systems": m.systems,
            "components": m.components,
            "remedy": m.remedy,
            "relevance_score": round(m.score, 6),
            "cosine_sim": round(m.cosine_sim, 4) if m.cosine_sim is not None else None,
            "cited": m.nhtsa_id in cited_ids,
            "superseded_count": m.superseded_count,
        }
        for m in visible
    ]

    log.info(
        "symptom-search vehicle=%s refused=%s matches=%d :: %s",
        vehicle_id,
        refused,
        len(matches_out),
        cost.summary(),
    )

    return {
        "answer": answer,
        "refused": refused,
        "confidence": round(confidence, 4),
        "model": used_model,
        "answer_generated": answer_generated,
        "matches": matches_out,
        "cited_ids": cited_ids,
        "cost": cost.as_dict(),
    }
