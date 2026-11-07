"""Job 2, second half: structured extraction with Claude Haiku.

The PDF itself (or the form ``pdf_input`` chose for it) goes to the Messages API when
someone is waiting (the in-app refresh) and to the Message Batches API when nobody is
(the CLI). Batch jobs are asynchronous and may take up to 24 hours, so submission and
collection are separate steps: submit, walk away, collect later.

The request shape and the error handling are the extraction bake-off's
(``src/eval/extraction_bakeoff.py``), copied rather than imported: that module is frozen
evidence.
"""

import asyncio
import base64
import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
import httpx

from src.anthropic_client import client as anthropic_client
from src.communications.severity import CATCH_ALL_TYPES, classify_from_document
from src.config import get_settings
from src.jobs import pdf_input
from src.usage import pricing
from src.usage.ledger import record_usage

log = logging.getLogger("llm")

# How many times a communication may fail LLM extraction before it stops being
# retried. Document selection keys on status='pending', so a failure that is
# written straight to 'failed' is never seen again; most LLM errors are
# transient and deserve another pass.
MAX_LLM_ATTEMPTS = 3

BATCH_DISCOUNT = pricing.BATCH_DISCOUNT

# Message Batches limits: requests per batch and bytes per batch.
BATCH_MAX_REQUESTS = 100_000
BATCH_MAX_BYTES = 200 * 1024 * 1024

# Kept only so the frozen bake-off module still imports; nothing in the stage uses it.
CHUNK_SIZE = 100

MAX_TOKENS = 8192
CALL_ATTEMPTS = 4  # tries per call, on top of the SDK's own retries
BACKOFF_SECONDS = 2.0  # doubled after each failed try
PROJECT = "schema-v2"

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
        "applicability_years": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Every model year the document states applies, as integers; empty if none.",
        },
        "applicability_models": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Model nameplates exactly as printed, without make or trim, e.g. 'F-150', 'Silverado EV'; empty if none.",
        },
        "applicability_conditions": {
            "type": "string",
            "description": "Engine, RPO/option code, build-date, plant or VIN qualifiers that narrow applicability; empty string if none.",
        },
        "audience": {
            "type": "string",
            "enum": ["owner", "dealer", "both"],
            "description": "Who the document addresses.",
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
        "applicability_years",
        "applicability_models",
        "applicability_conditions",
        "audience",
    ],
}

_RULES = """Extract structured data describing what this document says. Rules:
- Use only what the document states. Never infer a symptom, part, or remedy that is not present.
- If a field genuinely has no content in this document, return an empty array or empty string. Do not invent filler.
- symptoms are what someone would OBSERVE (noise, vibration, warning lamp, no-start), not internal causes.
- Keep each array item short: a noun phrase, not a sentence.
- applicability should capture model years and models even when they appear in a flattened table.
- doc_kind: service_procedure only when the document prescribes steps a technician performs; informational when it only informs or announces; recall only for a safety recall or campaign; customer_letter for a letter addressed to owners; warranty_admin for labor operations, claims and policy; other when none fits.
- audience: dealer when the document is addressed to dealers or technicians only; owner when addressed to vehicle owners; both when it speaks to both or is unclear.
- applicability_years are integers; applicability_models are the nameplates as printed, without make or trim; applicability_conditions carries engine, RPO, build-date, plant and VIN qualifiers.
- Never state that this document supersedes or is superseded by another unless the document says so explicitly, and then quote its direction exactly."""

TEXT_PROMPT = (
    "You are analysing a manufacturer communication (TSB, PI, recall, or warranty bulletin) "
    "issued for a motor vehicle. The text below was extracted from the original PDF, so table "
    "layout may be flattened into runs of adjacent values.\n\n"
    + _RULES
    + "\n\n--- DOCUMENT TEXT ---\n{text}\n--- END DOCUMENT TEXT ---"
)

PDF_PROMPT = (
    "You are analysing a manufacturer communication issued for a motor vehicle. "
    "The communication is attached to this message as a PDF.\n\n" + _RULES
)

MERGE_PROMPT = (
    "Below are {n} partial extractions of consecutive page ranges of one manufacturer "
    "communication, as JSON. Merge them into one record of the same shape: union the arrays "
    "without duplicates, keep every stated model year and model, write one summary and one "
    "remedy covering the whole document, choose the doc_kind the majority of parts chose, "
    "union applicability_years and applicability_models, keep the audience the majority of parts chose. "
    "Use only what the parts contain.\n\n"
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
    for key in ("summary", "remedy", "applicability", "doc_kind", "applicability_conditions"):
        if not isinstance(payload[key], str):
            return None, f"{key} must be a string, got {type(payload[key]).__name__}"

    allowed = RESPONSE_SCHEMA["properties"]["doc_kind"]["enum"]
    if payload["doc_kind"] not in allowed:
        return None, f"doc_kind {payload['doc_kind']!r} not in {allowed}"
    if payload["audience"] not in RESPONSE_SCHEMA["properties"]["audience"]["enum"]:
        return None, f"audience {payload['audience']!r} not in owner, dealer, both"
    if not payload["summary"].strip():
        return None, "summary is empty"
    if not isinstance(payload["applicability_years"], list):
        return None, "applicability_years must be a list"
    if not isinstance(payload["applicability_models"], list):
        return None, "applicability_models must be a list"

    years: list[int] = []
    for v in payload["applicability_years"]:
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and 1950 <= v <= 2100 and v not in years:
            years.append(v)
    models: list[str] = []
    for v in payload["applicability_models"]:
        if isinstance(v, str) and v.strip() and v.strip() not in models:
            models.append(v.strip())

    return {
        "summary": payload["summary"].strip(),
        "symptoms": [s.strip() for s in payload["symptoms"] if s.strip()],
        "systems": [s.strip() for s in payload["systems"] if s.strip()],
        "components": [s.strip() for s in payload["components"] if s.strip()],
        "remedy": payload["remedy"].strip(),
        "applicability": payload["applicability"].strip(),
        "doc_kind": payload["doc_kind"],
        "applicability_years": years,
        "applicability_models": models,
        "applicability_conditions": payload["applicability_conditions"].strip(),
        "audience": payload["audience"],
    }, None


def cost_of(tokens_in: int, tokens_out: int, batch: bool) -> float:
    return pricing.cost_of(get_settings().extraction_model, tokens_in, tokens_out, batch=batch)


def anthropic_schema() -> dict[str, Any]:
    """RESPONSE_SCHEMA in the form Anthropic's structured outputs take.

    The API requires ``additionalProperties: false`` on every object. A copy is made so
    RESPONSE_SCHEMA, which ``validate()`` shares, is never altered.
    """
    schema = copy.deepcopy(RESPONSE_SCHEMA)
    schema["additionalProperties"] = False
    return schema


def _params(content: Any) -> dict[str, Any]:
    """One request. No temperature and no thinking: low effort, structured output."""
    return {
        "model": get_settings().extraction_model,
        "max_tokens": MAX_TOKENS,
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": anthropic_schema()},
        },
        "messages": [{"role": "user", "content": content}],
    }


def _pdf_content(pdf_bytes: bytes) -> list[dict[str, Any]]:
    return [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(pdf_bytes).decode("ascii"),
            },
        },
        {"type": "text", "text": PDF_PROMPT},
    ]


def _text_content(text: str) -> str:
    return TEXT_PROMPT.format(text=text)


def _generation_config(temperature: float = 0.0):
    """Gemini generation settings. Only the frozen bake-off's Gemini arms still call this;
    the stage itself does not."""
    from google.genai import types

    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=temperature,
    )


def _client() -> anthropic.Anthropic:
    return anthropic_client()


async def _sleep(seconds: float) -> None:
    """Isolated so tests do not wait out a backoff."""
    await asyncio.sleep(seconds)


def _is_retryable(error: Exception) -> bool:
    """Rate limits, overload (529), other 5xx, timeouts and dropped connections, including an
    ``api_error`` or ``overloaded_error`` event in the middle of a stream."""
    if isinstance(
        error,
        (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
            httpx.TransportError,
        ),
    ):
        return True
    if isinstance(error, anthropic.APIStatusError):
        body = error.body if isinstance(error.body, dict) else {}
        inner = body.get("error") if isinstance(body.get("error"), dict) else {}
        return (
            error.status_code in (408, 409, 429)
            or error.status_code >= 500
            or "overloaded" in str(error).lower()
            or inner.get("type") in ("api_error", "overloaded_error")
        )
    return False


def _is_fatal(error: Exception) -> bool:
    """An API error about the account, not the document: bad key, no access, unknown model,
    no credit. Marking documents failed for it would burn their retry budget."""
    if isinstance(
        error,
        (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError),
    ):
        return True
    status = getattr(error, "status_code", None)
    return status in (401, 403, 404) or "credit balance" in str(error).lower()


class ExtractionFatal(RuntimeError):
    """The run cannot continue (see _is_fatal); nothing is marked failed."""


async def _call(client, params: dict) -> tuple[Optional[str], int, int, Optional[str]]:
    """One Messages call: ``(text, tokens_in, tokens_out, stop_reason)``.

    Retryable errors are retried with a doubling backoff; a fatal one raises
    ExtractionFatal. Any other API error, or running out of tries, comes back as
    ``(None, 0, 0, "api_error: ...")`` so the caller can fail that one document.
    """
    for attempt in range(CALL_ATTEMPTS):
        try:
            message = await asyncio.to_thread(lambda: client.messages.create(**params))
        except (anthropic.APIError, httpx.TransportError, TimeoutError) as e:
            if _is_fatal(e):
                raise ExtractionFatal(str(e)) from e
            if not _is_retryable(e):
                return None, 0, 0, f"api_error: {e}"
            if attempt == CALL_ATTEMPTS - 1:
                log.warning("still failing after %d tries: %s", CALL_ATTEMPTS, e)
                return None, 0, 0, f"api_error: {e}"
            await _sleep(BACKOFF_SECONDS * 2**attempt)
            continue
        text = next((b.text for b in message.content if b.type == "text"), None)
        return text, message.usage.input_tokens, message.usage.output_tokens, message.stop_reason
    return None, 0, 0, "api_error: no attempt made"  # unreachable


async def _meter(
    doc: dict,
    stats: "LLMStats",
    tokens_in: int,
    tokens_out: int,
    *,
    batch: bool,
    path: str,
    input_kind: str,
) -> None:
    """Ledger row and run totals for one response. Done before validation: a response that
    fails validation was still billed."""
    model = get_settings().extraction_model
    stats.tokens_in += tokens_in
    stats.tokens_out += tokens_out
    stats.cost_usd += pricing.cost_of(model, tokens_in, tokens_out, batch=batch)
    await record_usage(
        "llm_extract",
        model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        batch=batch,
        communication_id=doc["communication_id"],
        document_id=doc["id"],
        meta={"path": path, "input": input_kind, "project": PROJECT},
    )


async def _pending_documents(limit: int | None, vehicle_id: int | None = None) -> list[dict]:
    from src.db import get_pool
    from src.jobs.repository import vehicle_scope

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT d.id, d.url, d.extracted_text, d.extraction_method, d.page_count,
                   d.communication_id
            FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            WHERE d.llm_summary IS NULL
              AND c.status = 'pending'
              AND d.extracted_at IS NOT NULL
              AND {vehicle_scope('$1')}
            ORDER BY d.id
            {f'LIMIT {int(limit)}' if limit else ''}
            """,
            vehicle_id,
        )
    return [dict(r) for r in rows]


async def _plan_for(doc: dict) -> pdf_input.InputPlan:
    """How this document is presented to the model. Raises RuntimeError when its PDF cannot
    be had (archive pruned and the download failed)."""
    path = await pdf_input.ensure_archived(doc["url"])
    return await asyncio.to_thread(
        pdf_input.plan,
        path,
        doc["page_count"] or 1,
        doc["extraction_method"],
        bool(doc["extracted_text"]),
    )


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
                llm_tokens_in=$10, llm_tokens_out=$11, llm_cost_usd=$12, llm_at=now(),
                applicability_years=$13, applicability_models=$14,
                applicability_conditions=$15, audience=$16
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
            settings.extraction_model,
            tokens_in,
            tokens_out,
            cost_of(tokens_in, tokens_out, batch),
            clean["applicability_years"],
            clean["applicability_models"],
            clean["applicability_conditions"],
            clean["audience"],
        )


async def _mark_failed(communication_id: int, reason: str, retryable: bool = True) -> None:
    """Record an LLM failure, leaving it retryable until the budget runs out.

    Document selection requires `status = 'pending'`, so writing 'failed'
    immediately took the document out of circulation permanently. That is too
    harsh for an LLM error, which is usually transient: one 2018 F-150 bulletin
    was lost to a single malformed response even though the document itself was
    a perfectly ordinary two-page PDF.

    Until MAX_LLM_ATTEMPTS is reached the communication goes back to 'pending'
    with the reason recorded, so the next run picks it up. After that it is
    genuinely 'failed' and stops consuming budget.

    Pass retryable=False for something re-asking cannot fix.
    """
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Guard on status <> 'processed': a late LLM failure for one document
        # must not drag an already-processed communication back to failed.
        await conn.execute(
            """
            UPDATE communications
            SET attempts = attempts + 1,
                status_reason = $2,
                status = CASE
                    WHEN $3 AND attempts + 1 < $4 THEN 'pending'
                    ELSE 'failed'
                END
            WHERE id = $1 AND status <> 'processed'
            """,
            communication_id,
            reason[:500],
            retryable,
            MAX_LLM_ATTEMPTS,
        )


async def promote_completed_communications() -> int:
    """Flip a communication to `processed` once every one of its documents is done."""
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            promoted = await conn.fetch(
                """
                UPDATE communications c
                SET status='processed', processed_at=now(), status_reason=NULL
                WHERE c.status='pending'
                  AND EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id=c.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM comm_documents d
                      WHERE d.communication_id=c.id AND d.llm_summary IS NULL
                  )
                RETURNING c.id, c.communication_type
                """
            )
            for row in promoted:
                if row["communication_type"] not in CATCH_ALL_TYPES:
                    continue
                doc = await conn.fetchrow(
                    """
                    SELECT d.doc_kind, d.symptoms, d.remedy, d.audience
                    FROM comm_documents d WHERE d.communication_id=$1
                    ORDER BY d.id LIMIT 1
                    """,
                    row["id"],
                )
                if doc is None:
                    continue
                severity, reason = classify_from_document(
                    doc["doc_kind"], doc["symptoms"], doc["remedy"], doc["audience"]
                )
                await conn.execute(
                    "UPDATE communications SET severity=$2, severity_source='classifier' "
                    "WHERE id=$1",
                    row["id"],
                    severity,
                )
                log.debug("classified communication %s as %s: %s", row["id"], severity, reason)
    n = len(promoted)
    if n:
        log.info("promoted %d communication(s) to processed", n)
    return n


async def _validated_call(
    client, doc: dict, stats: LLMStats, content: Any, input_kind: str
) -> Optional[tuple[dict, int, int]]:
    """One extraction call, validated; a failure is retried once with identical parameters.

    Returns ``(clean, tokens_in, tokens_out)``, or None after marking the communication
    failed. A response cut off at max_tokens counts as a validation failure.
    """
    params = _params(content)
    err = "no attempt made"
    for attempt in (1, 2):
        text, ti, to, stop = await _call(client, params)
        if text is None and (stop or "").startswith("api_error"):
            stats.failed += 1
            stats.errors.append(f"doc {doc['id']}: {stop}")
            await _mark_failed(doc["communication_id"], f"LLM call failed: {stop}")
            return None
        await _meter(doc, stats, ti, to, batch=False, path="live", input_kind=input_kind)
        if stop == "max_tokens":
            clean, err = None, "response hit max_tokens"
        else:
            clean, err = validate(text)
        if err is None:
            return clean, ti, to
        if attempt == 1:
            stats.retried += 1
            log.warning("doc %s: %s -> retrying", doc["id"], err)
    stats.failed += 1
    stats.errors.append(f"doc {doc['id']}: {err}")
    await _mark_failed(doc["communication_id"], f"LLM schema violation: {err}")
    return None


async def _extract_one(
    client,
    doc: dict,
    stats: LLMStats,
    *,
    batch: bool = False,
    plan: pdf_input.InputPlan | None = None,
) -> None:
    """Extract one document through the Messages API and store it."""
    if plan is None:
        try:
            plan = await _plan_for(doc)
        except RuntimeError as e:
            stats.failed += 1
            stats.errors.append(f"doc {doc['id']}: {e}")
            await _mark_failed(doc["communication_id"], f"pdf unavailable: {e}"[:500], retryable=False)
            return

    if plan.kind == "pdf":
        result = await _validated_call(client, doc, stats, _pdf_content(plan.payloads[0]), "pdf")
    elif plan.kind == "text":
        result = await _validated_call(
            client, doc, stats, _text_content(doc["extracted_text"]), "text"
        )
    else:
        parts: list[dict] = []
        ti = to = 0
        for payload in plan.payloads:
            part = await _validated_call(client, doc, stats, _pdf_content(payload), "chunks")
            if part is None:
                return
            parts.append(part[0])
            ti += part[1]
            to += part[2]
        merge = MERGE_PROMPT.format(n=len(parts)) + "\n\n".join(
            f"Part {i}:\n{json.dumps(p)}" for i, p in enumerate(parts, 1)
        )
        merged = await _validated_call(client, doc, stats, merge, "chunks")
        result = None if merged is None else (merged[0], ti + merged[1], to + merged[2])
    if result is None:
        return
    clean, ti, to = result
    await _store(doc, clean, ti, to, batch=batch)
    stats.processed += 1


async def _extract_isolated(
    client,
    doc: dict,
    stats: LLMStats,
    *,
    plan: pdf_input.InputPlan | None = None,
) -> None:
    """``_extract_one``, except that an unexpected error costs this one document, not the run.

    Without this, gather() propagates the first stray exception (a database error storing
    one response, a PDF the planner chokes on) and every sibling still in flight is
    abandoned. ExtractionFatal still stops the run.
    """
    try:
        await _extract_one(client, doc, stats, plan=plan)
    except ExtractionFatal:
        raise
    except Exception as e:  # noqa: BLE001 - one bad document, keep going
        stats.failed += 1
        stats.errors.append(f"doc {doc['id']}: {type(e).__name__}: {e}")
        log.warning("doc %s: %s: %s", doc["id"], type(e).__name__, e)
        await _mark_failed(doc["communication_id"], f"LLM stage error: {type(e).__name__}: {e}")


async def run_sync(
    limit: int | None = None,
    concurrency: int | None = None,
    vehicle_id: int | None = None,
) -> LLMStats:
    """Process documents through the Messages API. Full price, immediate results.

    An ExtractionFatal (bad key, no credit) stops the run: the rest are cancelled and
    nothing is marked failed.
    """
    settings = get_settings()
    client = _client()
    stats = LLMStats()
    docs = await _pending_documents(limit, vehicle_id=vehicle_id)
    if not docs:
        log.info("no documents awaiting LLM extraction")
        return stats

    log.info("running live extraction over %d document(s)", len(docs))
    semaphore = asyncio.Semaphore(concurrency or settings.anthropic_extraction_concurrency)

    async def handle(doc: dict) -> None:
        async with semaphore:
            await _extract_isolated(client, doc, stats)

    tasks = [asyncio.create_task(handle(d)) for d in docs]
    try:
        await asyncio.gather(*tasks)
    except ExtractionFatal:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    await promote_completed_communications()
    log.info("live extraction complete :: %s", stats.summary())
    return stats


def _batch_chunks(requests: list[tuple[dict, int]]) -> list[list[dict]]:
    """Greedy split under both the request-count and the byte limit. A single request over
    the byte limit still gets a batch of its own; the API rejects it with a clear error."""
    out: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for request, nbytes in requests:
        if current and (len(current) >= BATCH_MAX_REQUESTS or size + nbytes > BATCH_MAX_BYTES):
            out.append(current)
            current, size = [], 0
        current.append(request)
        size += nbytes
    if current:
        out.append(current)
    return out


async def submit_batch(limit: int | None = None, vehicle_id: int | None = None) -> list[str]:
    """Submit pending pdf and text documents as Message Batches. Returns batch ids.

    Documents whose plan is ``chunks`` run live right here (they are several calls and a
    merge, which a batch cannot express), before any batch is created: an ExtractionFatal
    from them must not leave submitted batches behind whose ids the caller never gets.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = _client()
    docs = await _pending_documents(limit, vehicle_id=vehicle_id)
    if not docs:
        log.info("nothing to submit")
        return []

    stats = LLMStats()
    requests: list[tuple[dict, int]] = []
    live: list[tuple[dict, pdf_input.InputPlan]] = []
    for doc in docs:
        try:
            plan = await _plan_for(doc)
        except Exception as e:  # noqa: BLE001 - one unplannable PDF must not block the batch
            stats.failed += 1
            stats.errors.append(f"doc {doc['id']}: {e}")
            await _mark_failed(doc["communication_id"], f"pdf unavailable: {e}"[:500], retryable=False)
            continue
        if plan.kind == "chunks":
            live.append((doc, plan))
            continue
        content = (
            _pdf_content(plan.payloads[0])
            if plan.kind == "pdf"
            else _text_content(doc["extracted_text"])
        )
        params = _params(content)
        requests.append(
            (
                Request(
                    custom_id=str(doc["id"]), params=MessageCreateParamsNonStreaming(**params)
                ),
                len(json.dumps(params)),
            )
        )

    for doc, plan in live:
        await _extract_isolated(client, doc, stats, plan=plan)
    if live:
        log.info("chunked documents run live during submit :: %s", stats.summary())

    batch_ids: list[str] = []
    for chunk in _batch_chunks(requests):
        batch = await asyncio.to_thread(lambda c=chunk: client.messages.batches.create(requests=c))
        batch_ids.append(batch.id)
        log.info("submitted batch %s with %d document(s)", batch.id, len(chunk))
    return batch_ids


def _batch_failure_detail(result: Any) -> str:
    """The reason on an errored result: its ErrorResponse's own type is always "error"; the
    reason is the inner error object."""
    err = getattr(result, "error", None)
    inner = getattr(err, "error", None)
    detail = getattr(inner, "type", None) or getattr(err, "type", None)
    if getattr(inner, "message", None):
        detail = f"{detail} ({inner.message})"
    return f": {detail}" if detail else ""


async def collect_batch(
    batch_ids: list[str], poll_seconds: int = 30, max_wait: int = 3600
) -> LLMStats:
    """Poll batches and persist their results."""
    client = _client()
    stats = LLMStats()
    docs_by_id = {str(d["id"]): d for d in await _pending_documents(None)}

    for batch_id in batch_ids:
        waited = 0
        status = (await asyncio.to_thread(client.messages.batches.retrieve, batch_id)).processing_status
        while status != "ended":
            if waited >= max_wait:
                log.warning(
                    "batch %s still %s after %ds; leaving it running", batch_id, status, waited
                )
                break
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
            status = (
                await asyncio.to_thread(client.messages.batches.retrieve, batch_id)
            ).processing_status
        if status != "ended":
            continue

        results = await asyncio.to_thread(lambda b=batch_id: list(client.messages.batches.results(b)))
        dropped = 0
        for item in results:
            doc = docs_by_id.get(str(item.custom_id))
            if doc is None:
                # A paid result we cannot match to a pending document: count and log it
                # loudly rather than silently discarding what the run paid for.
                dropped += 1
                continue
            result = item.result
            if result.type != "succeeded":
                stats.failed += 1
                reason = f"batch {result.type}{_batch_failure_detail(result)}"
                stats.errors.append(f"doc {doc['id']}: {reason}")
                await _mark_failed(doc["communication_id"], reason)
                continue

            message = result.message
            ti, to = message.usage.input_tokens, message.usage.output_tokens
            input_kind = "pdf" if (doc["page_count"] or 1) <= pdf_input.MAX_DIRECT_PAGES else "text"
            await _meter(doc, stats, ti, to, batch=True, path="batch", input_kind=input_kind)
            text = next((b.text for b in message.content if b.type == "text"), None)
            if message.stop_reason == "max_tokens":
                clean, err = None, "response hit max_tokens"
            else:
                clean, err = validate(text)
            if err:
                stats.failed += 1
                stats.errors.append(f"doc {doc['id']}: {err}")
                await _mark_failed(doc["communication_id"], f"LLM schema violation: {err}")
                continue

            await _store(doc, clean, ti, to, batch=True)
            stats.processed += 1

        if dropped:
            msg = (
                f"batch {batch_id}: {dropped} response(s) could not be matched to a "
                f"pending document and were dropped"
            )
            log.warning(msg)
            stats.errors.append(msg)

    await promote_completed_communications()
    log.info("batch collection complete :: %s", stats.summary())
    return stats
