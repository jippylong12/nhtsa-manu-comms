"""Job 2, second half: structured extraction with Gemini.

Uses the Batch API by default (50% discount, and this pipeline has zero latency
sensitivity). Batch jobs are asynchronous and may take anywhere from seconds to
24 hours, so submission and collection are separate steps: submit, walk away,
collect later. A `--sync` mode exists for small or urgent runs at full price.

The prompt and schema are the ones validated in the A1 spike, which produced
24/24 valid parses at temperature 0 with no retries.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import fitz
from google import genai
from google.genai import types

from src.config import get_settings
from src.jobs.extract import archive_path

log = logging.getLogger("llm")

# Sync list price per 1M tokens for gemini-3.1-flash-lite; batch is half.
PRICE_IN, PRICE_OUT = 0.25 / 1e6, 1.50 / 1e6
BATCH_DISCOUNT = 0.5

# Inlined batch payloads are chunked so a single request stays a reasonable
# size; the corpus is ~1700 input tokens per document.
CHUNK_SIZE = 100

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentence plain-language summary of what this communication tells a technician or owner.",
        },
        "symptoms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Observable symptoms a driver or technician would notice. Empty if the document describes no symptom.",
        },
        "systems": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Affected vehicle systems, e.g. powertrain, HVAC, infotainment, brakes, electrical.",
        },
        "components": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific parts or modules named, e.g. ECM, fuel pump, A/C compressor.",
        },
        "remedy": {
            "type": "string",
            "description": "The corrective action prescribed. Empty string if none is given.",
        },
        "applicability": {
            "type": "string",
            "description": "Which vehicles this applies to (years, models, trims, build ranges) as stated in the document.",
        },
        "doc_kind": {
            "type": "string",
            "enum": [
                "service_procedure",
                "warranty_admin",
                "recall",
                "customer_letter",
                "informational",
                "other",
            ],
        },
    },
    "required": [
        "summary",
        "symptoms",
        "systems",
        "components",
        "remedy",
        "applicability",
        "doc_kind",
    ],
}

_RULES = """Extract structured data describing what this document says. Rules:
- Use only what the document states. Never infer a symptom, part, or remedy that is not present.
- If a field genuinely has no content in this document, return an empty array or empty string. Do not invent filler.
- symptoms are what someone would OBSERVE (noise, vibration, warning lamp, no-start), not internal causes.
- Keep each array item short: a noun phrase, not a sentence.
- applicability should capture model years and models even when they appear in a flattened table."""

TEXT_PROMPT = (
    "You are analysing a manufacturer communication (TSB, PI, recall, or warranty bulletin) "
    "issued for a motor vehicle. The text below was extracted from the original PDF, so table "
    "layout may be flattened into runs of adjacent values.\n\n"
    + _RULES
    + "\n\n--- DOCUMENT TEXT ---\n{text}\n--- END DOCUMENT TEXT ---"
)

VISION_PROMPT = (
    "You are analysing a manufacturer communication issued for a motor vehicle. The pages are "
    "provided as images because local text extraction produced unusable output.\n\n" + _RULES
)


@dataclass
class LLMStats:
    processed: int = 0
    failed: int = 0
    retried: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"processed={self.processed} failed={self.failed} retried={self.retried} "
            f"tokens_in={self.tokens_in} tokens_out={self.tokens_out} cost=${self.cost_usd:.4f} "
            f"errors={len(self.errors)}"
        )


def validate(payload: Any) -> tuple[Optional[dict], Optional[str]]:
    """Check a model response against the contract the database expects.

    Returns ``(clean, error)``. The schema is enforced server-side too, but a
    truncated or empty response still has to be caught here rather than written
    as a row of nulls.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            return None, f"invalid JSON: {e}"
    if not isinstance(payload, dict):
        return None, f"expected object, got {type(payload).__name__}"

    missing = [k for k in RESPONSE_SCHEMA["required"] if k not in payload]
    if missing:
        return None, f"missing fields: {', '.join(missing)}"

    for key in ("symptoms", "systems", "components"):
        if not isinstance(payload[key], list):
            return None, f"{key} must be a list, got {type(payload[key]).__name__}"
        if not all(isinstance(v, str) for v in payload[key]):
            return None, f"{key} must contain only strings"
    for key in ("summary", "remedy", "applicability", "doc_kind"):
        if not isinstance(payload[key], str):
            return None, f"{key} must be a string, got {type(payload[key]).__name__}"

    allowed = RESPONSE_SCHEMA["properties"]["doc_kind"]["enum"]
    if payload["doc_kind"] not in allowed:
        return None, f"doc_kind {payload['doc_kind']!r} not in {allowed}"
    if not payload["summary"].strip():
        return None, "summary is empty"

    return {
        "summary": payload["summary"].strip(),
        "symptoms": [s.strip() for s in payload["symptoms"] if s.strip()],
        "systems": [s.strip() for s in payload["systems"] if s.strip()],
        "components": [s.strip() for s in payload["components"] if s.strip()],
        "remedy": payload["remedy"].strip(),
        "applicability": payload["applicability"].strip(),
        "doc_kind": payload["doc_kind"],
    }, None


def cost_of(tokens_in: int, tokens_out: int, batch: bool) -> float:
    raw = tokens_in * PRICE_IN + tokens_out * PRICE_OUT
    return raw * BATCH_DISCOUNT if batch else raw


def _client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to backend/.env.")
    return genai.Client(api_key=settings.gemini_api_key)


def _generation_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=0.0,
    )


def _vision_parts(url: str) -> list[types.Part]:
    """Render a PDF's pages as images for the fallback path."""
    doc = fitz.open(archive_path(url))
    parts: list[types.Part] = [types.Part.from_text(text=VISION_PROMPT)]
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            parts.append(types.Part.from_bytes(data=pix.tobytes("png"), mime_type="image/png"))
    finally:
        doc.close()
    return parts


async def _pending_documents(limit: int | None) -> list[dict]:
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT d.id, d.url, d.extracted_text, d.extraction_method, d.communication_id
            FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            WHERE d.llm_summary IS NULL
              AND c.status = 'pending'
              AND (d.extracted_text IS NOT NULL OR d.extraction_method = 'vision-fallback')
            ORDER BY d.id
            {f'LIMIT {int(limit)}' if limit else ''}
            """
        )
    return [dict(r) for r in rows]


async def _store(doc: dict, clean: dict, tokens_in: int, tokens_out: int, batch: bool) -> None:
    from src.db import get_pool

    settings = get_settings()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE comm_documents
            SET llm_summary=$2, doc_kind=$3, symptoms=$4, systems=$5, components=$6,
                remedy=$7, applicability=$8, llm_model=$9,
                llm_tokens_in=$10, llm_tokens_out=$11, llm_cost_usd=$12, llm_at=now()
            WHERE id=$1
            """,
            doc["id"],
            clean["summary"],
            clean["doc_kind"],
            clean["symptoms"],
            clean["systems"],
            clean["components"],
            clean["remedy"],
            clean["applicability"],
            settings.gemini_model,
            tokens_in,
            tokens_out,
            cost_of(tokens_in, tokens_out, batch),
        )


async def _mark_failed(communication_id: int, reason: str) -> None:
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE communications
            SET status='failed', status_reason=$2, attempts=attempts+1
            WHERE id=$1
            """,
            communication_id,
            reason[:500],
        )


async def promote_completed_communications() -> int:
    """Flip a communication to `processed` once every one of its documents is done."""
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE communications c
            SET status='processed', processed_at=now(), status_reason=NULL
            WHERE c.status='pending'
              AND EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id=c.id)
              AND NOT EXISTS (
                  SELECT 1 FROM comm_documents d
                  WHERE d.communication_id=c.id AND d.llm_summary IS NULL
              )
            """
        )
    n = int(result.split()[-1]) if result else 0
    if n:
        log.info("promoted %d communication(s) to processed", n)
    return n


async def run_sync(limit: int | None = None, concurrency: int = 4) -> LLMStats:
    """Process documents with the standard API. Full price, immediate results."""
    settings = get_settings()
    client = _client()
    stats = LLMStats()
    docs = await _pending_documents(limit)
    if not docs:
        log.info("no documents awaiting LLM extraction")
        return stats

    log.info("running sync extraction over %d document(s)", len(docs))
    semaphore = asyncio.Semaphore(concurrency)

    async def handle(doc: dict) -> None:
        async with semaphore:
            for attempt in (1, 2):
                try:
                    if doc["extraction_method"] == "vision-fallback":
                        contents = [types.Content(role="user", parts=_vision_parts(doc["url"]))]
                    else:
                        contents = TEXT_PROMPT.format(text=doc["extracted_text"])

                    resp = await asyncio.to_thread(
                        client.models.generate_content,
                        model=settings.gemini_model,
                        contents=contents,
                        config=_generation_config(),
                    )
                    usage = resp.usage_metadata
                    ti = usage.prompt_token_count or 0
                    to = usage.candidates_token_count or 0
                    stats.tokens_in += ti
                    stats.tokens_out += to
                    stats.cost_usd += cost_of(ti, to, batch=False)

                    clean, err = validate(resp.text)
                    if err:
                        if attempt == 1:
                            stats.retried += 1
                            log.warning("doc %s: %s -> retrying", doc["id"], err)
                            continue
                        stats.failed += 1
                        stats.errors.append(f"doc {doc['id']}: {err}")
                        await _mark_failed(doc["communication_id"], f"LLM schema violation: {err}")
                        return

                    await _store(doc, clean, ti, to, batch=False)
                    stats.processed += 1
                    return
                except Exception as e:  # noqa: BLE001
                    if attempt == 1:
                        stats.retried += 1
                        await asyncio.sleep(2)
                        continue
                    stats.failed += 1
                    stats.errors.append(f"doc {doc['id']}: {type(e).__name__}: {e}")
                    await _mark_failed(doc["communication_id"], f"LLM call failed: {e}")

    await asyncio.gather(*(handle(d) for d in docs))
    await promote_completed_communications()
    log.info("sync extraction complete :: %s", stats.summary())
    return stats


async def submit_batch(limit: int | None = None) -> list[str]:
    """Submit pending documents as Batch API jobs. Returns job names.

    Vision-fallback documents are excluded: they are a rare path and are
    cheaper to run synchronously than to shuttle images through batch.
    """
    settings = get_settings()
    client = _client()
    docs = [
        d for d in await _pending_documents(limit) if d["extraction_method"] != "vision-fallback"
    ]
    if not docs:
        log.info("nothing to submit")
        return []

    job_names: list[str] = []
    for start in range(0, len(docs), CHUNK_SIZE):
        chunk = docs[start : start + CHUNK_SIZE]
        requests = [
            types.InlinedRequest(
                model=settings.gemini_model,
                contents=TEXT_PROMPT.format(text=d["extracted_text"]),
                config=_generation_config(),
                # Carries the document id through to the response so results
                # can be matched back without relying on ordering.
                metadata={"doc_id": str(d["id"])},
            )
            for d in chunk
        ]
        job = await asyncio.to_thread(
            client.batches.create,
            model=settings.gemini_model,
            src=requests,
            config=types.CreateBatchJobConfig(display_name=f"nhtsa-extract-{start // CHUNK_SIZE}"),
        )
        job_names.append(job.name)
        log.info("submitted batch %s with %d document(s) [%s]", job.name, len(chunk), job.state)

    return job_names


async def collect_batch(
    job_names: list[str], poll_seconds: int = 30, max_wait: int = 3600
) -> LLMStats:
    """Poll batch jobs and persist their results."""
    client = _client()
    stats = LLMStats()
    docs_by_id = {str(d["id"]): d for d in await _pending_documents(None)}

    for name in job_names:
        waited = 0
        job = await asyncio.to_thread(client.batches.get, name=name)
        while str(job.state) in (
            "JobState.JOB_STATE_PENDING",
            "JobState.JOB_STATE_QUEUED",
            "JobState.JOB_STATE_RUNNING",
        ):
            if waited >= max_wait:
                log.warning(
                    "batch %s still %s after %ds; leaving it running", name, job.state, waited
                )
                break
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
            job = await asyncio.to_thread(client.batches.get, name=name)

        if not job.dest or not job.dest.inlined_responses:
            log.warning("batch %s produced no inlined responses (state=%s)", name, job.state)
            continue

        for item in job.dest.inlined_responses:
            doc_id = (item.metadata or {}).get("doc_id")
            doc = docs_by_id.get(str(doc_id))
            if doc is None:
                continue
            if item.error or item.response is None:
                stats.failed += 1
                stats.errors.append(f"doc {doc_id}: batch error {item.error}")
                await _mark_failed(doc["communication_id"], f"batch error: {item.error}")
                continue

            usage = item.response.usage_metadata
            ti = (usage.prompt_token_count or 0) if usage else 0
            to = (usage.candidates_token_count or 0) if usage else 0
            stats.tokens_in += ti
            stats.tokens_out += to
            stats.cost_usd += cost_of(ti, to, batch=True)

            clean, err = validate(item.response.text)
            if err:
                stats.failed += 1
                stats.errors.append(f"doc {doc_id}: {err}")
                await _mark_failed(doc["communication_id"], f"LLM schema violation: {err}")
                continue

            await _store(doc, clean, ti, to, batch=True)
            stats.processed += 1

    await promote_completed_communications()
    log.info("batch collection complete :: %s", stats.summary())
    return stats
