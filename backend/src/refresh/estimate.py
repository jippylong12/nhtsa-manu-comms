"""What a refresh will cost and how long it will take.

Both figures are derived from measurement rather than hardcoded, so they
self-correct as the model, the prompt, or the pricing changes. The cost basis is
the `usage_events` ledger; the duration basis is the observed NHTSA fetch rate.

Everything here is presented as a range. A single number implies a precision
neither estimate has, and the Gemini batch queue in particular is server-side
and genuinely unpredictable.
"""

from dataclasses import dataclass
from typing import Optional

from src.db import get_pool

# Fallbacks for a cold ledger, from the Phase A measurements recorded on
# JIP-418: 389 documents for $0.2565 and 798 for $0.385, so $0.0005 to $0.0007
# per document via the Batch API. Only used until real spend exists.
FALLBACK_COST_PER_DOCUMENT = 0.0006

# NHTSA is polled one communication at a time with a delay between requests, and
# measured out at 309s for 389 bulletins.
SYNC_SECONDS_PER_BULLETIN = 0.8

# The Batch API is queued server-side. This is a floor, not an average: a batch
# has never come back faster, and it can take considerably longer.
BATCH_FLOOR_SECONDS = 120.0
# Marginal wall-clock per document once a batch is running, covering the PDF
# download and extraction that precede it.
SECONDS_PER_DOCUMENT = 0.9

# How wide to draw the range around the central estimate. Spend has been stable
# per document, so the cost band is tight; wall-clock is dominated by a queue we
# do not control, so its band is deliberately loose.
COST_SPREAD = 0.25
DURATION_SPREAD = 0.6


@dataclass
class Range:
    low: float
    high: float

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


async def cost_per_document() -> float:
    """Mean spend per processed document, from the ledger.

    Counts only rows carrying a `document_id`, which is per-document metering
    written by the LLM stage. Rows without one are the aggregates backfilled
    from `pipeline_runs` in migration 0003, and dividing those by a document
    count they do not correspond to would skew the figure.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT sum(cost_usd) FROM usage_events
                WHERE kind = 'llm_extract' AND document_id IS NOT NULL)    AS llm_cost,
              (SELECT count(DISTINCT document_id) FROM usage_events
                WHERE kind = 'llm_extract' AND document_id IS NOT NULL)    AS llm_docs,
              (SELECT sum(cost_usd)   FROM usage_events WHERE kind = 'embed') AS embed_cost,
              (SELECT sum(unit_count) FROM usage_events WHERE kind = 'embed') AS embed_units
            """
        )

    if not row["llm_docs"]:
        return FALLBACK_COST_PER_DOCUMENT

    per_doc = float(row["llm_cost"]) / row["llm_docs"]
    if row["embed_units"]:
        per_doc += float(row["embed_cost"]) / row["embed_units"]
    return per_doc


async def documents_per_bulletin() -> float:
    """How many PDFs a bulletin tends to carry, from the corpus.

    A preview knows how many bulletins are new but not how many documents they
    will turn out to hold, and documents are the billable unit.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        ratio = await conn.fetchval(
            """
            SELECT (SELECT count(*) FROM comm_documents)::float
                 / nullif((SELECT count(*) FROM communications), 0)
            """
        )
    # Close to 1.0 in practice. Never estimate below one document per bulletin;
    # under-quoting a price is worse than over-quoting it.
    return max(float(ratio or 1.0), 1.0)


def cost_range(documents: int, per_document: float) -> Range:
    mid = documents * per_document
    return Range(low=mid * (1 - COST_SPREAD), high=mid * (1 + COST_SPREAD))


def duration_range(new_bulletins: int, documents: int) -> Range:
    mid = (
        new_bulletins * SYNC_SECONDS_PER_BULLETIN
        + documents * SECONDS_PER_DOCUMENT
        + (BATCH_FLOOR_SECONDS if documents else 0.0)
    )
    return Range(low=mid * (1 - DURATION_SPREAD), high=mid * (1 + DURATION_SPREAD))


def describe_cost(r: Range) -> str:
    """A short human phrase. Cents below a dollar, never false precision."""
    if r.high < 0.01:
        return "under a cent"
    return f"about ${r.mid:.2f}"


def describe_duration(r: Range) -> str:
    mid = r.mid
    if mid < 90:
        return "under a minute" if mid < 60 else "about a minute"
    minutes = mid / 60
    if minutes < 10:
        return f"around {round(minutes)} minutes"
    return f"{int(r.low // 60)} to {int(r.high // 60)} minutes"


def summarise(new_bulletins: int, documents: int, per_document: float) -> dict:
    """The estimate block both the preview and the started run carry."""
    cost = cost_range(documents, per_document)
    duration = duration_range(new_bulletins, documents)
    return {
        "estimated_cost_low_usd": round(cost.low, 4),
        "estimated_cost_high_usd": round(cost.high, 4),
        "estimated_cost_text": describe_cost(cost),
        "estimated_seconds": int(duration.mid),
        "estimated_duration_text": describe_duration(duration),
        "cost_per_document_usd": round(per_document, 8),
    }


def optional_round(value: Optional[float], places: int = 6) -> Optional[float]:
    return None if value is None else round(value, places)
