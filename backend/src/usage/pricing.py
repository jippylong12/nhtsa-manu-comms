"""Published per-model rates. The single source of truth for cost in this repo.

Every billable path (corpus extraction, corpus embedding, query embedding,
answer composition) computes cost from this table, so ledger totals reconcile
with the provider's billing and a price change is a one-line edit here rather
than a hunt through job modules.

Rates are USD per token, from the Gemini published list prices.
"""

from dataclasses import dataclass

# Gemini Batch API is half list price. The corpus extraction job uses it.
BATCH_DISCOUNT = 0.5


@dataclass(frozen=True)
class ModelRate:
    """List price for one model, in USD per token."""

    input_per_token: float
    output_per_token: float


RATES: dict[str, ModelRate] = {
    # Text generation
    "gemini-3.1-flash-lite": ModelRate(0.25 / 1e6, 1.50 / 1e6),
    "gemini-3-flash-preview": ModelRate(0.30 / 1e6, 2.50 / 1e6),
    # Embeddings (output tokens are not billed)
    "gemini-embedding-001": ModelRate(0.15 / 1e6, 0.0),
}

# Used when a model id is not in RATES, so an unknown model is costed
# conservatively instead of silently reporting zero spend.
FALLBACK = RATES["gemini-3.1-flash-lite"]


def rate_for(model: str) -> ModelRate:
    return RATES.get(model, FALLBACK)


def cost_of(model: str, tokens_in: int, tokens_out: int = 0, *, batch: bool = False) -> float:
    """Cost of one model call in USD."""
    rate = rate_for(model)
    raw = tokens_in * rate.input_per_token + tokens_out * rate.output_per_token
    return raw * BATCH_DISCOUNT if batch else raw
