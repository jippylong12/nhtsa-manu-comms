"""Per-query cost accounting for symptom search.

A single symptom query costs one embedding call (to vectorise the query) plus,
in the B2 API, one LLM composition call. Both are tracked here so a query's
total cost can be logged and surfaced. Prices are Gemini list prices per input
token; keep them in sync with ``src/jobs/embed.py`` and ``src/jobs/llm.py``.
"""

from dataclasses import dataclass

from src.usage import pricing

# Re-exported from the canonical rate table in src/usage/pricing.py so the
# per-query figures shown to the user and the ledger totals cannot drift apart.
PRICE_EMBED_IN = pricing.rate_for("gemini-embedding-001").input_per_token
PRICE_LLM_IN = pricing.rate_for("gemini-3.5-flash-lite").input_per_token
PRICE_LLM_OUT = pricing.rate_for("gemini-3.5-flash-lite").output_per_token


def approx_tokens(text: str) -> int:
    """Rough token count from characters.

    The embeddings endpoint does not reliably return usage metadata, so query
    cost is approximated the same way the corpus embedding job does: ~4 chars
    per token. One query is a handful of tokens, so the absolute error is
    fractions of a millionth of a cent.
    """
    return max(1, len(text) // 4)


def llm_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost of one composition call. Falls back to Flash-Lite pricing for an
    unrecognised model rather than silently reporting zero."""
    return pricing.cost_of(model, tokens_in, tokens_out)


@dataclass
class QueryCost:
    """The full cost of answering one symptom query."""

    embed_tokens: int = 0
    embed_cost_usd: float = 0.0
    llm_model: str = ""
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_cost_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return self.embed_cost_usd + self.llm_cost_usd

    def record_embed(self, text: str) -> None:
        self.embed_tokens = approx_tokens(text)
        self.embed_cost_usd = self.embed_tokens * PRICE_EMBED_IN

    def record_llm(self, model: str, tokens_in: int, tokens_out: int) -> None:
        self.llm_model = model
        self.llm_tokens_in = tokens_in
        self.llm_tokens_out = tokens_out
        self.llm_cost_usd = llm_cost(model, tokens_in, tokens_out)

    def as_dict(self) -> dict:
        return {
            "embed_tokens": self.embed_tokens,
            "embed_cost_usd": round(self.embed_cost_usd, 8),
            "llm_model": self.llm_model,
            "llm_tokens_in": self.llm_tokens_in,
            "llm_tokens_out": self.llm_tokens_out,
            "llm_cost_usd": round(self.llm_cost_usd, 8),
            "total_usd": round(self.total_usd, 8),
        }

    def summary(self) -> str:
        return (
            f"embed={self.embed_tokens}tok/${self.embed_cost_usd:.8f} "
            f"llm[{self.llm_model or 'none'}]="
            f"{self.llm_tokens_in}+{self.llm_tokens_out}tok/${self.llm_cost_usd:.8f} "
            f"total=${self.total_usd:.8f}"
        )
