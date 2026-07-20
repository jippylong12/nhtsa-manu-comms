"""Job 2, third half: embeddings for semantic retrieval.

Embeds a composed representation of each processed document so Phase B can
match a described symptom against the corpus. The composed input is the LLM
summary plus symptoms and systems rather than the raw extracted text: the raw
text is dominated by boilerplate (copyright headers, warranty tables, dealer
instructions) that would drown the semantic signal.
"""

import asyncio
import logging
import math
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from src.config import get_settings

log = logging.getLogger("embed")

# gemini-embedding-001 list price per 1M input tokens.
PRICE_IN = 0.15 / 1e6
BATCH_SIZE = 50


@dataclass
class EmbedStats:
    embedded: int = 0
    failed: int = 0
    tokens_in: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"embedded={self.embedded} failed={self.failed} "
            f"tokens_in={self.tokens_in} cost=${self.cost_usd:.4f} errors={len(self.errors)}"
        )


def compose_input(row: dict) -> str:
    """Build the text that gets embedded.

    Summary first so it dominates, then the tag vocabulary that a symptom query
    is most likely to rhyme with.
    """
    parts = [row.get("llm_summary") or ""]
    if row.get("symptoms"):
        parts.append("Symptoms: " + ", ".join(row["symptoms"]))
    if row.get("systems"):
        parts.append("Systems: " + ", ".join(row["systems"]))
    if row.get("components"):
        parts.append("Components: " + ", ".join(row["components"]))
    return "\n".join(p for p in parts if p.strip())


def normalize(vector: list[float]) -> list[float]:
    """Scale to unit length.

    gemini-embedding-001 emits 3072 dimensions natively and is only normalised
    at that size. Requesting a Matryoshka-truncated 1536 returns a vector that
    is no longer unit length, so cosine distance would be subtly wrong without
    this. Google's own guidance is to renormalise after truncation.
    """
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def to_pgvector(vector: list[float]) -> str:
    """pgvector's text input format."""
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


async def embed_pending(limit: int | None = None, force: bool = False) -> EmbedStats:
    from src.db import get_pool

    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    client = genai.Client(api_key=settings.gemini_api_key)
    stats = EmbedStats()
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, llm_summary, symptoms, systems, components
            FROM comm_documents
            WHERE llm_summary IS NOT NULL
              {'' if force else 'AND embedding IS NULL'}
            ORDER BY id
            {f'LIMIT {int(limit)}' if limit else ''}
            """
        )

    if not rows:
        log.info("no documents awaiting embedding")
        return stats

    log.info("embedding %d document(s) at %d dimensions", len(rows), settings.embedding_dimensions)

    for start in range(0, len(rows), BATCH_SIZE):
        chunk = [dict(r) for r in rows[start : start + BATCH_SIZE]]
        texts = [compose_input(r) for r in chunk]
        try:
            resp = await asyncio.to_thread(
                client.models.embed_content,
                model=settings.gemini_embedding_model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=settings.embedding_dimensions,
                ),
            )
        except Exception as e:  # noqa: BLE001
            stats.failed += len(chunk)
            msg = f"batch at offset {start}: {type(e).__name__}: {e}"
            stats.errors.append(msg)
            log.error(msg)
            continue

        # A silent length mismatch between inputs and returned embeddings would
        # otherwise misalign every doc after the gap with the wrong vector.
        if len(resp.embeddings) != len(chunk):
            msg = (
                f"batch at offset {start}: got {len(resp.embeddings)} embeddings "
                f"for {len(chunk)} inputs; skipping this batch to avoid misalignment"
            )
            stats.failed += len(chunk)
            stats.errors.append(msg)
            log.error(msg)
            continue

        async with pool.acquire() as conn:
            for row, emb in zip(chunk, resp.embeddings):
                values = normalize(list(emb.values))
                if len(values) != settings.embedding_dimensions:
                    stats.failed += 1
                    stats.errors.append(
                        f"doc {row['id']}: got {len(values)} dims, "
                        f"expected {settings.embedding_dimensions}"
                    )
                    continue
                if not any(values):
                    # An all-zero vector has undefined cosine distance in
                    # pgvector; skip rather than poison KNN results.
                    stats.failed += 1
                    stats.errors.append(f"doc {row['id']}: zero-magnitude embedding, skipped")
                    continue
                await conn.execute(
                    """
                    UPDATE comm_documents
                    SET embedding = $2::vector, embedding_model = $3, embedded_at = now()
                    WHERE id = $1
                    """,
                    row["id"],
                    to_pgvector(values),
                    settings.gemini_embedding_model,
                )
                stats.embedded += 1

        # The embeddings endpoint does not always return usage metadata, so
        # cost is approximated from characters when it is absent.
        approx_tokens = sum(len(t) for t in texts) // 4
        stats.tokens_in += approx_tokens
        stats.cost_usd += approx_tokens * PRICE_IN
        log.info("  embedded %d/%d", min(start + BATCH_SIZE, len(rows)), len(rows))

    log.info("embedding complete :: %s", stats.summary())
    return stats


async def embed_query(text: str) -> list[float]:
    """Embed a search query. Uses RETRIEVAL_QUERY, the asymmetric counterpart
    to the RETRIEVAL_DOCUMENT task type used for the corpus."""
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    resp = await asyncio.to_thread(
        client.models.embed_content,
        model=settings.gemini_embedding_model,
        contents=[text],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=settings.embedding_dimensions,
        ),
    )
    return normalize(list(resp.embeddings[0].values))
