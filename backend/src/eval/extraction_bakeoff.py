"""Extraction bake-off harness (JIP-1567).

Sends one fixed, stratified sample of already-parsed documents through several arms
and writes one ``results.jsonl`` per arm, so a later step can score them against
the production extractions and a judge.

    G            gemini-3.5-flash-lite, extracted text   Gemini Batch API
    H-text       claude-haiku-5-5,      extracted text   Message Batches (canceled, superseded)
    H-pdf        claude-haiku-5-5,      the PDF itself   Message Batches (canceled, superseded)
    H-text-live  claude-haiku-5-5,      extracted text   Messages API, all 300 documents
    H-pdf-live   claude-haiku-5-5,      the PDF itself   Messages API, all 300 documents
    S-text       claude-sonnet-5-5,     extracted text   Messages API, the 100-document subset
    S-pdf        claude-sonnet-5-5,     the PDF itself   Messages API, the 100-document subset

Every arm uses the production prompt and response schema from ``src.jobs.llm``
unchanged. The two Haiku batches sat queued for 25+ minutes and were canceled; the
live arms re-run them (and add Sonnet) through the synchronous Messages API at list
price. Run as ``python -m src.eval.extraction_bakeoff <command>``:

    sample                      pick the sample and write extraction_sample.json
    sample-sonnet               pick 100 of those 300 and write extraction_sample_sonnet100.json
    submit --arm G|H-text|H-pdf|all                      (batch arms)
    collect [--wait SECONDS]    write results.jsonl for every batch arm that ended
    run-live --arm H-text-live|H-pdf-live|S-text|S-pdf|all-live [--concurrency N]
    status [--remote]           estimates, batch state and spend

State lives in ``backend/.bakeoff/<run>/<arm>/`` (``requests.jsonl``, ``batch.json``,
``results.jsonl``, and ``live.json`` for a live arm). ``submit`` and ``run-live`` are
the commands that spend money; both refuse past ``CAP_USD``.
"""

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import logging
import pathlib
import random
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import anthropic
from google.genai import types as genai_types

from src.jobs import extract, llm
from src.usage import pricing
from src.usage.ledger import record_usage

log = logging.getLogger("extraction_bakeoff")

# --------------------------------------------------------------------------- constants

# Raised from 10 by Marcus 2026-11-06 for the Sonnet arms. One budget for the whole
# experiment: the arms and the judge, across every run directory.
CAP_USD = 20.0
SEED = 20261106
SAMPLE_SIZE = 300
MIN_PER_CELL = 5
LARGEST_FORCED = 10

GEMINI_MODEL = "gemini-3.5-flash-lite"
HAIKU_MODEL = "claude-haiku-5-5"
SONNET_MODEL = "claude-sonnet-5-5"
HAIKU_MAX_TOKENS = 8192  # every Anthropic arm, Sonnet included, so the arms cannot drift
SONNET_SUBSET_SIZE = 100

# Gemini: chunks of 100 as in llm.submit_batch, and inline payloads must stay under
# 20 MB (ai.google.dev batch docs), so a chunk also closes at 18 MB.
GEMINI_CHUNK_REQUESTS = llm.CHUNK_SIZE
GEMINI_CHUNK_BYTES = 18 * 1024 * 1024
# Anthropic: a batch is limited to 100,000 requests or 256 MB, whichever comes first
# (platform.claude.com batch-processing). A batch closes at 200 MB of request JSON,
# which leaves headroom for the envelope.
ANTHROPIC_BATCH_REQUESTS = 100_000
ANTHROPIC_BATCH_BYTES = 200 * 1024 * 1024
# A Messages request is limited to 32 MB, and base64 grows a file by 4/3, so a PDF
# over ~24 MB can never fit; 23 MB leaves room for the prompt and the schema.
MAX_PDF_BYTES = 23 * 1024 * 1024

PDF_TOKENS_PER_PAGE = 3000
PDF_OUTPUT_TOKENS = 300

# Batch arms: submitted, then collected.
ARMS = ("G", "H-text", "H-pdf")
# Live arms: run through the synchronous Messages API, one result line per document.
LIVE_ARMS = ("H-text-live", "H-pdf-live", "S-text", "S-pdf")
ALL_ARMS = (*ARMS, *LIVE_ARMS)
# Canceled before they ran; the live arms replace them in scoring.
SUPERSEDED_ARMS = ("H-text", "H-pdf")
# What scoring, the judge and the report use unless told otherwise.
DEFAULT_ARMS = ("G", *LIVE_ARMS)
SONNET_ARMS = ("S-text", "S-pdf")

LIVE_CONCURRENCY = 6
LIVE_ATTEMPTS = 4  # tries per call, on top of the SDK's own retries
LIVE_BACKOFF_SECONDS = 2.0  # doubled after each failed try

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
BAKEOFF_ROOT = BACKEND_DIR / ".bakeoff"
SAMPLE_PATH = pathlib.Path(__file__).with_name("extraction_sample.json")
SONNET_SAMPLE_PATH = pathlib.Path(__file__).with_name("extraction_sample_sonnet100.json")

SAMPLE_FIELDS = (
    "doc_id",
    "nhtsa_id",
    "url",
    "doc_kind",
    "communication_type",
    "llm_tokens_in",
    "llm_tokens_out",
    "page_count",
)

# The rules text is the production one; only the framing sentence differs, because
# VISION_PROMPT's "pages are provided as images" would be false for an attached PDF.
PDF_PROMPT = (
    "You are analysing a manufacturer communication issued for a motor vehicle. "
    "The communication is attached to this message as a PDF.\n\n" + llm._RULES
)


# --------------------------------------------------------------------------- sampling


def _cell(row: dict) -> tuple:
    return (row.get("doc_kind"), row.get("communication_type"))


def _cell_sort_key(cell: tuple) -> tuple:
    return tuple("" if part is None else str(part) for part in cell)


def select_sample(
    rows: Iterable[dict],
    size: int = SAMPLE_SIZE,
    seed: int = SEED,
    min_per_cell: int = MIN_PER_CELL,
    largest: int = LARGEST_FORCED,
) -> list[dict]:
    """Stratified sample of ``size`` documents, identical for identical input.

    Forced in first: every ``vision-fallback`` document and the ``largest`` by
    ``llm_tokens_in``. The rest is spread over ``(doc_kind, communication_type)``
    cells: a cell with at least ``min_per_cell`` documents is guaranteed that many
    (forced documents count toward it), and the remainder is shared in proportion
    to what each cell still has to give. Input order does not matter.
    """
    ordered = sorted(rows, key=lambda r: r["doc_id"])

    forced: dict[int, dict] = {
        r["doc_id"]: r for r in ordered if r.get("extraction_method") == "vision-fallback"
    }
    by_size = sorted(
        (r for r in ordered if r.get("llm_tokens_in") is not None and r["doc_id"] not in forced),
        key=lambda r: (-r["llm_tokens_in"], r["doc_id"]),
    )
    for r in by_size[:largest]:
        forced[r["doc_id"]] = r

    pool: dict[tuple, list[dict]] = {}
    forced_in: dict[tuple, int] = {}
    totals: dict[tuple, int] = {}
    for r in ordered:
        totals[_cell(r)] = totals.get(_cell(r), 0) + 1
        if r["doc_id"] in forced:
            forced_in[_cell(r)] = forced_in.get(_cell(r), 0) + 1
        else:
            pool.setdefault(_cell(r), []).append(r)

    cells = sorted(pool, key=_cell_sort_key)
    remaining = max(size - len(forced), 0)

    # Floors first, so a small cell is never starved by proportional rounding.
    take: dict[tuple, int] = {}
    for c in cells:
        floor = min(min_per_cell, totals[c]) if totals[c] >= min_per_cell else 0
        take[c] = min(max(floor - forced_in.get(c, 0), 0), len(pool[c]))
    if sum(take.values()) > remaining:
        raise ValueError(f"size {size} is too small for the per-cell floors")

    # Then share what is left in proportion to each cell's unused documents.
    spare = remaining - sum(take.values())
    capacity = {c: len(pool[c]) - take[c] for c in cells}
    total_capacity = sum(capacity.values())
    spare = min(spare, total_capacity)
    if spare and total_capacity:
        exact = {c: spare * capacity[c] / total_capacity for c in cells}
        share = {c: int(exact[c]) for c in cells}
        leftover = spare - sum(share.values())
        by_remainder = sorted(cells, key=lambda c: (-(exact[c] - share[c]), _cell_sort_key(c)))
        for c in by_remainder:
            if leftover <= 0:
                break
            if share[c] < capacity[c]:
                share[c] += 1
                leftover -= 1
        for c in cells:
            take[c] += share[c]

    rng = random.Random(seed)
    chosen = list(forced.values())
    for c in cells:
        chosen.extend(rng.sample(pool[c], take[c]))
    chosen.sort(key=lambda r: r["doc_id"])
    return [{k: r.get(k) for k in SAMPLE_FIELDS} for r in chosen]


def select_sonnet_subset(
    rows: Iterable[dict], size: int = SONNET_SUBSET_SIZE, seed: int = SEED
) -> list[dict]:
    """``size`` documents drawn from the sample for the Sonnet arms, identical for identical input.

    Stratified over the same ``(doc_kind, communication_type)`` cells as the sample:
    every cell that has a usable document gets one, and the rest is shared in
    proportion to what each cell has left (largest remainder). A usable document has
    extracted text (a recorded ``llm_tokens_in``) and a URL, so neither Sonnet arm
    wastes a call on a row it cannot send. Unusable documents are used only if the
    usable ones cannot fill ``size``. Input order does not matter.
    """
    ordered = sorted(rows, key=lambda r: r["doc_id"])
    usable = [r for r in ordered if r.get("llm_tokens_in") is not None and r.get("url")]
    usable_ids = {r["doc_id"] for r in usable}
    spare_rows = [r for r in ordered if r["doc_id"] not in usable_ids]

    pool: dict[tuple, list[dict]] = {}
    for r in usable:
        pool.setdefault(_cell(r), []).append(r)
    cells = sorted(pool, key=_cell_sort_key)
    if size < len(cells):
        raise ValueError(f"size {size} is smaller than the {len(cells)} cells")

    take = {c: 1 for c in cells}
    spare = min(size - len(cells), sum(len(pool[c]) - 1 for c in cells))
    capacity = {c: len(pool[c]) - 1 for c in cells}
    total_capacity = sum(capacity.values())
    if spare and total_capacity:
        exact = {c: spare * capacity[c] / total_capacity for c in cells}
        share = {c: int(exact[c]) for c in cells}
        leftover = spare - sum(share.values())
        for c in sorted(cells, key=lambda c: (-(exact[c] - share[c]), _cell_sort_key(c))):
            if leftover <= 0:
                break
            if share[c] < capacity[c]:
                share[c] += 1
                leftover -= 1
        for c in cells:
            take[c] += share[c]

    rng = random.Random(seed)
    chosen = []
    for c in cells:
        chosen.extend(rng.sample(pool[c], take[c]))
    short = size - len(chosen)
    if short > 0 and spare_rows:
        chosen.extend(rng.sample(spare_rows, min(short, len(spare_rows))))
    chosen.sort(key=lambda r: r["doc_id"])
    return [{k: r.get(k) for k in SAMPLE_FIELDS} for r in chosen]


def write_sonnet_sample(
    src: pathlib.Path = SAMPLE_PATH,
    dest: Optional[pathlib.Path] = None,
    size: int = SONNET_SUBSET_SIZE,
) -> list[dict]:
    """Pick the Sonnet subset from the sample file (no database, no network) and write it."""
    subset = select_sonnet_subset(json.loads(pathlib.Path(src).read_text()), size=size)
    dest = pathlib.Path(dest or SONNET_SAMPLE_PATH)
    dest.write_text(json.dumps(subset, indent=1, ensure_ascii=False) + "\n")
    return subset


_CANDIDATES_SQL = """
    SELECT d.id AS doc_id, c.nhtsa_id, d.url, d.doc_kind, c.communication_type,
           d.llm_tokens_in, d.llm_tokens_out, d.page_count, d.extraction_method
    FROM comm_documents d
    JOIN communications c ON c.id = d.communication_id
    WHERE (d.llm_summary IS NOT NULL AND d.extracted_text IS NOT NULL)
       OR d.extraction_method = 'vision-fallback'
    ORDER BY d.id
"""


async def _fetch_candidates() -> list[dict]:
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        return [dict(r) for r in await conn.fetch(_CANDIDATES_SQL)]


def strata_table(sample: list[dict]) -> list[tuple[Any, Any, int]]:
    """``(doc_kind, communication_type, count)`` per cell, largest first."""
    counts: dict[tuple, int] = {}
    for r in sample:
        counts[_cell(r)] = counts.get(_cell(r), 0) + 1
    return sorted(
        ((k[0], k[1], n) for k, n in counts.items()),
        key=lambda t: (-t[2], _cell_sort_key((t[0], t[1]))),
    )


async def write_sample(path: pathlib.Path = SAMPLE_PATH, size: int = SAMPLE_SIZE) -> list[dict]:
    """Select the sample from the database and write it as stable, sorted JSON."""
    sample = select_sample(await _fetch_candidates(), size=size)
    path.write_text(json.dumps(sample, indent=1, ensure_ascii=False) + "\n")
    return sample


# --------------------------------------------------------------------------- requests


@dataclass
class _Request:
    """One document's request for one arm, or the reason it has none."""

    doc_id: int
    nhtsa_id: str
    custom_id: str
    params: Any = None  # provider-native request; None when the document has no input
    error: Optional[str] = None
    size: int = 0  # bytes counted against the provider's batch limit


@dataclass
class _Raw:
    """One provider response, before validation."""

    doc_id: int
    text: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    error: Optional[str] = None
    billed: bool = False  # the provider returned a response, so it metered tokens
    stop_reason: Optional[str] = None


def _anthropic_schema() -> dict[str, Any]:
    """The production response schema in the form Anthropic's structured outputs take.

    The API requires ``additionalProperties: false`` on every object. A copy is
    made so ``llm.RESPONSE_SCHEMA``, which Gemini and ``validate()`` share, is
    never altered.
    """
    schema = copy.deepcopy(llm.RESPONSE_SCHEMA)
    schema["additionalProperties"] = False
    return schema


def _chunks(requests: list[_Request], max_requests: int, max_bytes: int) -> list[list[_Request]]:
    """Greedy split into batches under both a request-count and a byte limit.

    A single request over the byte limit still gets a batch of its own; the
    provider will reject it with a clear error rather than this harness hiding it.
    """
    out: list[list[_Request]] = []
    current: list[_Request] = []
    size = 0
    for r in requests:
        if current and (len(current) >= max_requests or size + r.size > max_bytes):
            out.append(current)
            current, size = [], 0
        current.append(r)
        size += r.size
    if current:
        out.append(current)
    return out


class _GeminiArm:
    """Arm G: gemini-3.5-flash-lite over extracted text, inlined Batch API requests."""

    name = "G"
    model = GEMINI_MODEL
    provider = "gemini"
    pdf = False
    live = False
    input_mode = "text"

    def build_requests(self, docs: list[dict]) -> list[_Request]:
        out = []
        for d in docs:
            req = _Request(d["doc_id"], d.get("nhtsa_id"), str(d["doc_id"]))
            if not d.get("text"):
                req.error = "no extracted text"
            else:
                prompt = llm.TEXT_PROMPT.format(text=d["text"])
                req.params = genai_types.InlinedRequest(
                    model=self.model,
                    contents=prompt,
                    config=llm._generation_config(temperature=0.0),
                    # Carries the document id through to the response, as
                    # llm.submit_batch does, so results never rely on ordering.
                    metadata={"doc_id": str(d["doc_id"])},
                )
                req.size = len(prompt.encode("utf-8"))
            out.append(req)
        return out

    def describe(self, req: _Request) -> Any:
        p = req.params
        return {
            "model": p.model,
            "contents": p.contents,
            "temperature": p.config.temperature,
            "response_mime_type": p.config.response_mime_type,
            "response_schema": "llm.RESPONSE_SCHEMA",
            "metadata": p.metadata,
        }

    def split(self, requests: list[_Request]) -> list[list[_Request]]:
        return _chunks(requests, GEMINI_CHUNK_REQUESTS, GEMINI_CHUNK_BYTES)

    def submit(self, client, chunk: list[_Request], label: str) -> str:
        job = client.batches.create(
            model=self.model,
            src=[r.params for r in chunk],
            config=genai_types.CreateBatchJobConfig(display_name=label),
        )
        return job.name

    _TERMINAL = {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_PARTIALLY_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }

    @staticmethod
    def _state_name(job) -> str:
        state = job.state
        return getattr(state, "name", None) or str(state).split(".")[-1]

    def state(self, client, batch_id: str) -> tuple[bool, str]:
        name = self._state_name(client.batches.get(name=batch_id))
        return name in self._TERMINAL, name

    def collect(self, client, batch_id: str, doc_ids: list[int]) -> list[_Raw]:
        job = client.batches.get(name=batch_id)
        dest = job.dest
        if not dest or not dest.inlined_responses:
            reason = f"batch {self._state_name(job)}: no responses"
            return [_Raw(i, error=reason) for i in doc_ids]
        out = []
        for item in dest.inlined_responses:
            doc_id = (item.metadata or {}).get("doc_id")
            if doc_id is None:
                continue
            if item.error or item.response is None:
                out.append(_Raw(int(doc_id), error=f"batch error {item.error}"))
                continue
            usage = item.response.usage_metadata
            # Thinking tokens are billed at the output rate but are not part of
            # candidates_token_count, so they are added here.
            tokens_out = (
                (usage.candidates_token_count or 0)
                + (getattr(usage, "thoughts_token_count", None) or 0)
                if usage
                else 0
            )
            out.append(
                _Raw(
                    int(doc_id),
                    text=item.response.text,
                    tokens_in=(usage.prompt_token_count or 0) if usage else 0,
                    tokens_out=tokens_out,
                    billed=True,
                )
            )
        return out


class _AnthropicArm:
    """The Anthropic arms: Haiku or Sonnet, over extracted text or the PDF.

    ``live=False`` arms (H-text, H-pdf) go through the Message Batches API; ``live=True``
    arms (H-text-live, H-pdf-live, S-text, S-pdf) go through the synchronous Messages
    API. Both build their request here, so the parameters cannot drift between arms.

    No ``temperature`` and no ``thinking`` is sent: the current API rejects the first,
    and ``effort`` is the knob. ``output_config.format`` is the structured-outputs
    form ``{"type": "json_schema", "schema": ...}``.
    """

    provider = "anthropic"

    def __init__(self, name: str, pdf: bool, model: str = HAIKU_MODEL, live: bool = False):
        self.name = name
        self.pdf = pdf
        self.model = model
        self.live = live
        self.input_mode = "pdf" if pdf else "text"

    def _params(self, content: Any) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": HAIKU_MAX_TOKENS,
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": _anthropic_schema()},
            },
            "messages": [{"role": "user", "content": content}],
        }

    def build_requests(self, docs: list[dict]) -> list[_Request]:
        out = []
        for d in docs:
            req = _Request(d["doc_id"], d.get("nhtsa_id"), str(d["doc_id"]))
            if self.pdf:
                if not d.get("pdf_b64"):
                    req.error = d.get("pdf_error") or "pdf unavailable"
                else:
                    req.params = self._params(
                        [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": d["pdf_b64"],
                                },
                            },
                            {"type": "text", "text": PDF_PROMPT},
                        ]
                    )
            elif not d.get("text"):
                req.error = "no extracted text"
            else:
                req.params = self._params(llm.TEXT_PROMPT.format(text=d["text"]))
            if req.params is not None:
                req.size = len(json.dumps(req.params))
            out.append(req)
        return out

    def describe(self, req: _Request) -> Any:
        """The request as recorded on disk; a PDF's base64 is summarised, not copied."""
        params = copy.deepcopy(req.params)
        if self.pdf:
            source = params["messages"][0]["content"][0]["source"]
            data = source["data"]
            source["data"] = (
                f"<base64 {len(data)} chars sha256 {hashlib.sha256(data.encode()).hexdigest()}>"
            )
        return params

    def split(self, requests: list[_Request]) -> list[list[_Request]]:
        return _chunks(requests, ANTHROPIC_BATCH_REQUESTS, ANTHROPIC_BATCH_BYTES)

    def submit(self, client, chunk: list[_Request], label: str) -> str:
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        batch = client.messages.batches.create(
            requests=[
                Request(custom_id=r.custom_id, params=MessageCreateParamsNonStreaming(**r.params))
                for r in chunk
            ]
        )
        return batch.id

    def state(self, client, batch_id: str) -> tuple[bool, str]:
        status = client.messages.batches.retrieve(batch_id).processing_status
        return status == "ended", str(status)

    def collect(self, client, batch_id: str, doc_ids: list[int]) -> list[_Raw]:
        out = []
        # Results arrive in any order; custom_id is the document id.
        for item in client.messages.batches.results(batch_id):
            doc_id = int(item.custom_id)
            result = item.result
            if result.type != "succeeded":
                # An errored result carries an ErrorResponse whose own type is always
                # "error"; the reason is the inner error object.
                err = getattr(result, "error", None)
                inner = getattr(err, "error", None)
                detail = getattr(inner, "type", None) or getattr(err, "type", None)
                if getattr(inner, "message", None):
                    detail = f"{detail} ({inner.message})"
                out.append(
                    _Raw(doc_id, error=f"batch {result.type}" + (f": {detail}" if detail else ""))
                )
                continue
            message = result.message
            text = next((b.text for b in message.content if b.type == "text"), None)
            out.append(
                _Raw(
                    doc_id,
                    text=text,
                    tokens_in=message.usage.input_tokens,
                    tokens_out=message.usage.output_tokens,
                    billed=True,
                    stop_reason=message.stop_reason,
                )
            )
        return out


_ARM_IMPLS = {
    "G": _GeminiArm(),
    "H-text": _AnthropicArm("H-text", pdf=False),
    "H-pdf": _AnthropicArm("H-pdf", pdf=True),
    "H-text-live": _AnthropicArm("H-text-live", pdf=False, live=True),
    "H-pdf-live": _AnthropicArm("H-pdf-live", pdf=True, live=True),
    "S-text": _AnthropicArm("S-text", pdf=False, model=SONNET_MODEL, live=True),
    "S-pdf": _AnthropicArm("S-pdf", pdf=True, model=SONNET_MODEL, live=True),
}


def arm_mode(arm: str) -> str:
    """``"live"`` (Messages API, list price) or ``"batch"`` (half price)."""
    return "live" if _ARM_IMPLS[arm].live else "batch"


def arm_input(arm: str) -> str:
    """``"pdf"`` (the PDF as a document block) or ``"text"`` (the extracted text)."""
    return _ARM_IMPLS[arm].input_mode


def build_requests(arm: str, docs: list[dict]) -> list[_Request]:
    """The requests ``arm`` would send for ``docs`` (each carrying ``text`` or ``pdf_b64``)."""
    return _ARM_IMPLS[arm].build_requests(docs)


# --------------------------------------------------------------------------- clients and inputs


def _gemini_client():
    from src.gemini import client

    return client()


def _anthropic_client():
    from src.anthropic_client import client

    return client()


def _client_for(arm: str):
    return _gemini_client() if _ARM_IMPLS[arm].provider == "gemini" else _anthropic_client()


async def _fetch_texts(doc_ids: list[int]) -> dict[int, Optional[str]]:
    """Extracted text per document, read from the database."""
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, extracted_text FROM comm_documents WHERE id = ANY($1::bigint[])",
            list(doc_ids),
        )
    return {r["id"]: r["extracted_text"] for r in rows}


async def _fetch_pdfs(sample: list[dict]) -> dict[int, tuple[Optional[str], Optional[str]]]:
    """``(base64, error)`` per document. The archive is pruned after processing, so
    this goes back to NHTSA through ``extract.download`` (free, and cached on disk)."""
    import httpx

    from src.config import get_settings

    sem = asyncio.Semaphore(get_settings().pdf_download_concurrency)
    out: dict[int, tuple[Optional[str], Optional[str]]] = {}

    async with httpx.AsyncClient(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)"},
    ) as http:

        async def one(entry: dict) -> None:
            async with sem:
                path, err, _ = await extract.download(http, entry["url"])
            if path is None:
                log.warning("doc %s: pdf unavailable (%s)", entry["doc_id"], err)
                out[entry["doc_id"]] = (None, "pdf unavailable")
                return
            size = path.stat().st_size
            if size > MAX_PDF_BYTES:
                out[entry["doc_id"]] = (None, f"pdf too large ({size} bytes > {MAX_PDF_BYTES})")
                return
            # standard_b64encode never inserts newlines.
            out[entry["doc_id"]] = (
                base64.standard_b64encode(path.read_bytes()).decode("ascii"),
                None,
            )

        await asyncio.gather(*(one(e) for e in sample))
    return out


async def _hydrate(arm: str, sample: list[dict]) -> list[dict]:
    """The sample entries plus what ``arm`` needs to build a request (text or PDF)."""
    docs = [dict(e) for e in sample]
    if _ARM_IMPLS[arm].pdf:
        pdfs = await _fetch_pdfs(sample)
        for d in docs:
            d["pdf_b64"], d["pdf_error"] = pdfs.get(d["doc_id"], (None, "pdf unavailable"))
    else:
        texts = await _fetch_texts([d["doc_id"] for d in docs])
        for d in docs:
            d["text"] = texts.get(d["doc_id"])
    return docs


# --------------------------------------------------------------------------- run state


def load_sample(path: pathlib.Path = SAMPLE_PATH) -> list[dict]:
    return json.loads(pathlib.Path(path).read_text())


def new_run_dir() -> pathlib.Path:
    return BAKEOFF_ROOT / datetime.now().strftime("%Y%m%d-%H%M")


def latest_run_dir() -> Optional[pathlib.Path]:
    runs = sorted(p for p in BAKEOFF_ROOT.glob("*") if p.is_dir()) if BAKEOFF_ROOT.exists() else []
    return runs[-1] if runs else None


def _arm_dir(run_dir: pathlib.Path, arm: str) -> pathlib.Path:
    return pathlib.Path(run_dir) / arm


def _read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    # Split on "\n" only: str.splitlines() also breaks on U+0085, U+2028 and other
    # separators that json.dumps(ensure_ascii=False) leaves raw inside a string.
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def _write_jsonl(path: pathlib.Path, rows: Iterable[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _is_submitted(run_dir: pathlib.Path, arm: str) -> bool:
    return (_arm_dir(run_dir, arm) / "batch.json").exists()


def _is_collected(run_dir: pathlib.Path, arm: str) -> bool:
    return (_arm_dir(run_dir, arm) / "results.jsonl").exists()


# --------------------------------------------------------------------------- spend


class SpendCapExceeded(RuntimeError):
    """A submit that would take the run past ``CAP_USD``."""


@dataclass
class RunSpend:
    collected: float = 0.0  # actual, from results.jsonl
    committed: float = 0.0  # estimated, for arms submitted but not yet collected
    per_arm: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.collected + self.committed


def estimate_cost(arm: str, sample: list[dict]) -> float:
    """Cost of sending ``sample`` through ``arm``: batch rate for a batch arm, list price
    for a live arm.

    Text arms use the token counts the production run recorded for each document
    (Haiku and Sonnet tokenise differently, so this is an estimate, not a
    prediction). PDF arms have no recorded count: 3000 input tokens per page plus
    300 output tokens per document. Documents with no text are not sent to text arms.
    """
    impl = _ARM_IMPLS[arm]
    total = 0.0
    for d in sample:
        if impl.pdf:
            tin = max(d.get("page_count") or 1, 1) * PDF_TOKENS_PER_PAGE
            tout = PDF_OUTPUT_TOKENS
        else:
            if d.get("llm_tokens_in") is None:
                continue
            tin, tout = d["llm_tokens_in"], d.get("llm_tokens_out") or 0
        total += pricing.cost_of(impl.model, tin, tout, batch=not impl.live)
    return total


def run_spend(run_dir: pathlib.Path) -> RunSpend:
    """Money this run has spent (collected) or is committed to (submitted, estimated)."""
    spend = RunSpend()
    for arm in ALL_ARMS:
        # A live arm's results.jsonl grows line by line, so it counts as spent as it goes.
        if _is_collected(run_dir, arm):
            cost = sum(r["cost_usd"] for r in _read_jsonl(_arm_dir(run_dir, arm) / "results.jsonl"))
            spend.collected += cost
            spend.per_arm[arm] = cost
        elif _is_submitted(run_dir, arm):
            cost = _read_json(_arm_dir(run_dir, arm) / "batch.json").get("estimate_usd", 0.0)
            spend.committed += cost
            spend.per_arm[arm] = cost
    return spend


def judge_spend(run_dir: pathlib.Path) -> float:
    """What the blind judge (``bakeoff_score``) has spent in ``run_dir``."""
    path = pathlib.Path(run_dir) / "judge.jsonl"
    return sum(r.get("cost_usd") or 0.0 for r in _read_jsonl(path)) if path.exists() else 0.0


def experiment_spend(run_dir: pathlib.Path) -> float:
    """Spent or committed by the whole bake-off: arms and judge, in ``run_dir`` and in
    every other run under ``BAKEOFF_ROOT``. The cap is one budget for the experiment,
    so starting a new run directory never resets it."""
    dirs = {pathlib.Path(run_dir).resolve()}
    if BAKEOFF_ROOT.exists():
        dirs |= {p.resolve() for p in BAKEOFF_ROOT.iterdir() if p.is_dir()}
    return sum(run_spend(d).total + judge_spend(d) for d in dirs)


def check_cap(run_dir: pathlib.Path, arms: list[str], sample: list[dict]) -> dict[str, float]:
    """Refuse (raise) when spend so far plus these arms' estimates would pass the cap."""
    estimates = {arm: estimate_cost(arm, sample) for arm in arms}
    spent = experiment_spend(run_dir)
    if spent + sum(estimates.values()) > CAP_USD:
        detail = ", ".join(f"{a} ${e:.4f}" for a, e in estimates.items())
        raise SpendCapExceeded(
            f"refusing to submit: already spent or committed ${spent:.4f} across the "
            f"bake-off, estimate {detail}, total ${spent + sum(estimates.values()):.4f} "
            f"exceeds the cap of ${CAP_USD:.2f}. Nothing was sent."
        )
    return estimates


# --------------------------------------------------------------------------- submit


async def submit(
    arm: str,
    run_dir: pathlib.Path,
    sample_path: pathlib.Path = SAMPLE_PATH,
) -> dict[str, list[str]]:
    """Send ``arm`` (or ``all`` the arms not yet submitted) as provider batches.

    Order matters: the cap is checked first, then the key (a missing key fails
    here, clearly), and only then is anything read from the database or NHTSA.
    """
    run_dir = pathlib.Path(run_dir)
    sample = load_sample(sample_path)
    if arm == "all":
        arms = [a for a in ARMS if not _is_submitted(run_dir, a)]
    elif arm in ARMS:
        if _is_submitted(run_dir, arm):
            raise RuntimeError(f"arm {arm} was already submitted in {run_dir}")
        arms = [arm]
    else:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS} or 'all'")
    if not arms:
        return {}

    estimates = check_cap(run_dir, arms, sample)
    clients = {a: _client_for(a) for a in arms}

    submitted: dict[str, list[str]] = {}
    for a in arms:
        impl = _ARM_IMPLS[a]
        docs = await _hydrate(a, sample)
        requests = impl.build_requests(docs)
        sendable = [r for r in requests if r.params is not None]

        arm_dir = _arm_dir(run_dir, a)
        arm_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(
            arm_dir / "requests.jsonl",
            (
                {
                    "doc_id": r.doc_id,
                    "nhtsa_id": r.nhtsa_id,
                    "custom_id": r.custom_id,
                    "arm": a,
                    "model": impl.model,
                    "bytes": r.size,
                    "error": r.error,
                    "request": impl.describe(r) if r.params is not None else None,
                }
                for r in requests
            ),
        )

        batches = []
        submitted_at = datetime.now(timezone.utc).isoformat()
        for n, chunk in enumerate(impl.split(sendable)):
            batch_id = impl.submit(clients[a], chunk, f"bakeoff-{run_dir.name}-{a}-{n}")
            batches.append({"id": batch_id, "doc_ids": [r.doc_id for r in chunk]})
            log.info("%s: submitted batch %s with %d request(s)", a, batch_id, len(chunk))
            # Written after every batch, not once at the end: if a later batch fails,
            # the ones already paid for are on disk, the arm counts as submitted, and
            # a retry refuses instead of sending them a second time.
            (arm_dir / "batch.json").write_text(
                json.dumps(
                    {
                        "arm": a,
                        "model": impl.model,
                        "submitted_at": submitted_at,
                        "estimate_usd": estimates[a],
                        "batches": batches,
                    },
                    indent=1,
                )
                + "\n"
            )
        submitted[a] = [b["id"] for b in batches]
    return submitted


# --------------------------------------------------------------------------- collect


def _result_line(arm: str, request: dict, raw: Optional[_Raw], run_dir: pathlib.Path) -> dict:
    impl = _ARM_IMPLS[arm]
    output, valid, error = None, False, None
    tin = tout = 0
    if request.get("error"):
        error = request["error"]
    elif raw is None:
        error = "no result returned for this request"
    else:
        tin, tout = raw.tokens_in, raw.tokens_out
        if raw.error:
            error = raw.error
        else:
            output, error = llm.validate(raw.text if raw.text is not None else "")
            valid = error is None
            if error and raw.stop_reason not in (None, "end_turn"):
                error = f"{error} (stop_reason={raw.stop_reason})"
    return {
        "doc_id": request["doc_id"],
        "nhtsa_id": request["nhtsa_id"],
        "arm": arm,
        "model": impl.model,
        "output": output,
        "valid": valid,
        "error": error,
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": (
            pricing.cost_of(impl.model, tin, tout, batch=not impl.live)
            if raw and raw.billed
            else 0.0
        ),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        **({"mode": "live"} if impl.live else {}),
    }


async def collect(
    run_dir: pathlib.Path,
    poll_seconds: float = 30,
    max_wait: float = 0,
) -> dict[str, str]:
    """Write ``results.jsonl`` for every submitted arm whose batches have all ended.

    An arm that is still running is left alone (and reported), so ``collect`` can
    simply be run again. An arm that already has ``results.jsonl`` is never
    collected twice, which keeps its usage from being recorded twice.
    """
    run_dir = pathlib.Path(run_dir)
    report: dict[str, str] = {}
    clients: dict[str, Any] = {}

    for arm in ARMS:
        if not _is_submitted(run_dir, arm):
            continue
        if _is_collected(run_dir, arm):
            report[arm] = "already collected"
            continue

        impl = _ARM_IMPLS[arm]
        if impl.provider not in clients:
            clients[impl.provider] = _client_for(arm)
        client = clients[impl.provider]
        batch = _read_json(_arm_dir(run_dir, arm) / "batch.json")

        waited = 0.0
        while True:
            states = [impl.state(client, b["id"]) for b in batch["batches"]]
            if all(done for done, _ in states):
                break
            if waited >= max_wait:
                break
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
        if not all(done for done, _ in states):
            report[arm] = "still running: " + ", ".join(label for _, label in states)
            continue

        raws: dict[int, _Raw] = {}
        for b in batch["batches"]:
            for raw in impl.collect(client, b["id"], b["doc_ids"]):
                raws[raw.doc_id] = raw
            for doc_id in b["doc_ids"]:
                raws.setdefault(doc_id, None)  # asked for, never answered

        requests = _read_jsonl(_arm_dir(run_dir, arm) / "requests.jsonl")
        lines = [_result_line(arm, r, raws.get(r["doc_id"]), run_dir) for r in requests]
        for r, line in zip(requests, lines):
            raw = raws.get(r["doc_id"])
            if raw is not None and raw.billed:
                # Metered before anything is judged: an invalid response was still billed.
                await record_usage(
                    kind="llm_extract",
                    model=impl.model,
                    tokens_in=raw.tokens_in,
                    tokens_out=raw.tokens_out,
                    batch=True,
                    meta={"bakeoff": arm, "run": str(run_dir), "doc_id": r["doc_id"]},
                )
        _write_jsonl(_arm_dir(run_dir, arm) / "results.jsonl", lines)
        ok = sum(1 for line in lines if line["valid"])
        report[arm] = f"collected {len(lines)} ({ok} valid)"
    return report


# --------------------------------------------------------------------------- live arms


def _sleep(seconds: float) -> None:
    """Isolated so tests do not wait out a backoff."""
    time.sleep(seconds)


def _is_retryable(error: Exception) -> bool:
    """Rate limits, overload (529), other 5xx, timeouts and dropped connections."""
    if isinstance(
        error,
        (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError),
    ):
        return True
    if isinstance(error, anthropic.APIStatusError):
        return (
            error.status_code in (408, 409, 429)
            or error.status_code >= 500
            or "overloaded" in str(error).lower()
        )
    return False


class LiveCallFatal(RuntimeError):
    """An API error about the account, not the document (bad key, no access, unknown
    model, no credit). Written as a result line it would mark every remaining document
    done, so a re-run after the fix would skip them; the arm stops instead."""


def _is_fatal(error: Exception) -> bool:
    if isinstance(
        error,
        (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError),
    ):
        return True
    status = getattr(error, "status_code", None)
    return status in (401, 403, 404) or "credit balance" in str(error).lower()


def _call_live(client, params: dict, doc_id: int) -> Optional[_Raw]:
    """One document through the Messages API, retried on top of the SDK's own retries.

    Returns the response as a ``_Raw``; an unbilled error ``_Raw`` for an API error that
    retrying cannot fix (a 400, say); ``None`` when every try hit a retryable error, so
    the caller leaves the document for the next run. Anything that is not an API error
    is a bug and propagates.
    """
    for attempt in range(LIVE_ATTEMPTS):
        try:
            message = client.messages.create(**params)
        except anthropic.APIError as e:
            if not _is_retryable(e):
                if _is_fatal(e):
                    raise LiveCallFatal(f"doc {doc_id}: {e}; nothing written for it") from e
                status = getattr(e, "status_code", None)
                detail = getattr(e, "message", None) or str(e)
                return _Raw(doc_id, error=f"api error{f' {status}' if status else ''}: {detail}")
            if attempt == LIVE_ATTEMPTS - 1:
                log.warning("doc %s: still failing after %d tries: %s", doc_id, LIVE_ATTEMPTS, e)
                return None
            _sleep(LIVE_BACKOFF_SECONDS * 2**attempt)
            continue
        text = next((b.text for b in message.content if b.type == "text"), None)
        return _Raw(
            doc_id,
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
            billed=True,
            stop_reason=message.stop_reason,
        )
    return None  # unreachable: the last attempt returns or raises


def live_worst_case_usd(arm: str, req: _Request, doc: dict) -> float:
    """The most one call could cost at list price: a generous prompt estimate plus every
    output token. Text is 2 characters a token; a PDF is ``page_count`` * 3000 tokens
    plus its prompt text."""
    impl = _ARM_IMPLS[arm]
    if impl.pdf:
        pages = max(doc.get("page_count") or 1, 1)
        tin = pages * PDF_TOKENS_PER_PAGE + len(PDF_PROMPT) // 2
    else:
        tin = len(req.params["messages"][0]["content"]) // 2
    return pricing.cost_of(impl.model, tin, HAIKU_MAX_TOKENS, batch=False)


@dataclass
class LiveResult:
    """What one ``run_live`` did for one arm, and where the arm stands now."""

    arm: str
    lines: int = 0  # in results.jsonl, this run and earlier ones
    valid: int = 0
    errors: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0  # summed over every run, from live.json
    sent: int = 0  # documents this run got a response or a final API error for
    retry_later: int = 0  # documents whose every try failed; the next run sends them
    stopped_at_cap: bool = False


def format_live_result(r: LiveResult) -> str:
    line = (
        f"{r.arm}: lines {r.lines} valid {r.valid} errors {r.errors} "
        f"tokens {r.tokens_in}/{r.tokens_out} cost ${r.cost_usd:.4f} wall {r.wall_seconds:.0f}s"
    )
    if r.retry_later:
        line += f" retry_later {r.retry_later}"
    if r.stopped_at_cap:
        line += " stopped_at_cap"
    return line


class _Budget:
    """The cap for one live arm: money spent, plus the worst case of calls in flight."""

    def __init__(self, spent: float):
        self.spent = spent
        self.reserved = 0.0
        # Counted, not inferred from ``reserved == 0``: adding and subtracting floats can
        # leave a residue like 2.8e-17, and a worker waiting on it would spin forever.
        self.in_flight = 0

    def reserve(self, worst: float) -> bool:
        if self.spent + self.reserved + worst > CAP_USD:
            return False
        self.reserved += worst
        self.in_flight += 1
        return True

    def settle(self, worst: float, cost: float) -> None:
        self.in_flight -= 1
        self.reserved = self.reserved - worst if self.in_flight else 0.0
        self.spent += cost


def _live_sample(arm: str, sample_path: pathlib.Path, sonnet_sample_path: pathlib.Path):
    """The Sonnet arms run the 100-document subset; the Haiku arms run all of the sample."""
    return load_sample(sonnet_sample_path if arm in SONNET_ARMS else sample_path)


def _live_wall(arm_dir: pathlib.Path) -> float:
    path = arm_dir / "live.json"
    return float(_read_json(path).get("wall_seconds") or 0.0) if path.exists() else 0.0


def _write_live_json(arm_dir: pathlib.Path, arm: str, started: datetime, finished: datetime):
    """Wall time of every run of this arm: the first start, the last finish, and the sum."""
    path = arm_dir / "live.json"
    previous = _read_json(path) if path.exists() else {}
    seconds = (finished - started).total_seconds()
    runs = [
        *previous.get("runs", []),
        {
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "wall_seconds": seconds,
        },
    ]
    path.write_text(
        json.dumps(
            {
                "arm": arm,
                "model": _ARM_IMPLS[arm].model,
                "started_at": runs[0]["started_at"],
                "finished_at": finished.isoformat(),
                "wall_seconds": sum(r["wall_seconds"] for r in runs),
                "runs": runs,
            },
            indent=1,
        )
        + "\n"
    )


async def _run_live_arm(
    arm: str,
    client,
    run_dir: pathlib.Path,
    sample: list[dict],
    concurrency: int,
) -> LiveResult:
    impl = _ARM_IMPLS[arm]
    arm_dir = _arm_dir(run_dir, arm)
    arm_dir.mkdir(parents=True, exist_ok=True)
    results_path = arm_dir / "results.jsonl"
    done = {r["doc_id"] for r in _read_jsonl(results_path)} if results_path.exists() else set()
    todo = [e for e in sample if e["doc_id"] not in done]
    result = LiveResult(arm)
    started = datetime.now(timezone.utc)

    if todo:
        docs = await _hydrate(arm, todo)
        by_id = {d["doc_id"]: d for d in docs}
        requests = impl.build_requests(docs)
        budget = _Budget(experiment_spend(run_dir))
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=max(concurrency, 1))
        stop = False

        # Appended and flushed one line at a time, so a crash or Ctrl-C loses nothing
        # that finished. A document is only "done" once its line is on disk.
        with results_path.open("a") as fh:

            def write(line: dict) -> None:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                fh.flush()

            def request_row(req: _Request) -> dict:
                return {"doc_id": req.doc_id, "nhtsa_id": req.nhtsa_id, "error": req.error}

            # Documents with nothing to send cost nothing and get their error line first.
            for req in requests:
                if req.params is None:
                    write(_result_line(arm, request_row(req), None, run_dir))
            queue = deque(r for r in requests if r.params is not None)

            async def worker() -> None:
                nonlocal stop
                while queue and not stop:
                    req = queue.popleft()
                    worst = live_worst_case_usd(arm, req, by_id[req.doc_id])
                    while not budget.reserve(worst):
                        if budget.in_flight == 0:
                            # Nothing in flight to wait for: this call cannot fit.
                            result.stopped_at_cap = stop = True
                            return
                        await asyncio.sleep(0.05)  # in-flight calls may settle for less
                    try:
                        raw = await loop.run_in_executor(
                            pool, _call_live, client, req.params, req.doc_id
                        )
                    except BaseException:
                        budget.settle(worst, 0.0)
                        raise
                    line = _result_line(arm, request_row(req), raw, run_dir) if raw else None
                    budget.settle(worst, line["cost_usd"] if line else 0.0)
                    if line is None:
                        result.retry_later += 1
                        continue
                    write(line)
                    result.sent += 1
                    if raw.billed:
                        # Metered after the line is safely on disk: a crash between the two
                        # loses a ledger row, never pays for the document twice.
                        await record_usage(
                            kind="llm_extract",
                            model=impl.model,
                            tokens_in=raw.tokens_in,
                            tokens_out=raw.tokens_out,
                            batch=False,
                            meta={
                                "bakeoff": arm,
                                "run": str(run_dir),
                                "doc_id": req.doc_id,
                                "mode": "live",
                            },
                        )

            workers = [asyncio.ensure_future(worker()) for _ in range(max(concurrency, 1))]
            try:
                await asyncio.gather(*workers)
            except BaseException:
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
                _write_live_json(arm_dir, arm, started, datetime.now(timezone.utc))
    else:
        log.info("%s: every document already has a result line", arm)

    rows = _read_jsonl(results_path) if results_path.exists() else []
    result.lines = len(rows)
    result.valid = sum(1 for r in rows if r["valid"])
    result.errors = result.lines - result.valid
    result.tokens_in = sum(r["tokens_in"] for r in rows)
    result.tokens_out = sum(r["tokens_out"] for r in rows)
    result.cost_usd = sum(r["cost_usd"] for r in rows)
    result.wall_seconds = _live_wall(arm_dir)
    return result


async def run_live(
    arm: str,
    run_dir: pathlib.Path,
    sample_path: pathlib.Path = SAMPLE_PATH,
    sonnet_sample_path: Optional[pathlib.Path] = None,
    concurrency: int = LIVE_CONCURRENCY,
) -> dict[str, LiveResult]:
    """Run a live arm (or ``all-live``, in order) through the synchronous Messages API.

    The key is checked first (a missing one fails here, clearly), and only then is
    anything read from the database or NHTSA. Each finished document is appended to
    ``<run>/<arm>/results.jsonl`` at once, and a re-run skips the documents already
    there. Before every call its worst case must fit under ``CAP_USD`` minus what the
    bake-off has spent and what calls in flight could still cost; otherwise the arm
    stops with ``stopped_at_cap``.
    """
    run_dir = pathlib.Path(run_dir)
    if arm == "all-live":
        arms = list(LIVE_ARMS)
    elif arm in LIVE_ARMS:
        arms = [arm]
    else:
        raise ValueError(f"{arm!r} is not a live arm; expected one of {LIVE_ARMS} or 'all-live'")
    sonnet_sample_path = pathlib.Path(sonnet_sample_path or SONNET_SAMPLE_PATH)

    client = _anthropic_client()
    out: dict[str, LiveResult] = {}
    for a in arms:
        sample = _live_sample(a, sample_path, sonnet_sample_path)
        out[a] = await _run_live_arm(a, client, run_dir, sample, concurrency)
    return out


# --------------------------------------------------------------------------- status


def status(
    run_dir: pathlib.Path,
    sample_path: pathlib.Path = SAMPLE_PATH,
    remote: bool = False,
    sonnet_sample_path: Optional[pathlib.Path] = None,
) -> str:
    """Per-arm estimate, batch state and spend. Local only unless ``remote``.

    Batch arms are estimated at the batch rate, live arms at list price; the Sonnet
    arms over the Sonnet subset (no estimate if that file has not been written).
    """
    run_dir = pathlib.Path(run_dir)
    sample = load_sample(sample_path)
    sonnet_path = pathlib.Path(sonnet_sample_path or SONNET_SAMPLE_PATH)
    sonnet = load_sample(sonnet_path) if sonnet_path.exists() else []
    spend = run_spend(run_dir)
    lines = [
        f"run {run_dir}",
        f"sample {len(sample)} documents, Sonnet subset {len(sonnet)}",
        "",
    ]
    clients: dict[str, Any] = {}
    for arm in ARMS:
        impl = _ARM_IMPLS[arm]
        head = f"{arm:<7} {impl.model:<22} estimate ${estimate_cost(arm, sample):.4f}"
        if not _is_submitted(run_dir, arm):
            lines.append(f"{head}  not submitted")
            continue
        batch = _read_json(_arm_dir(run_dir, arm) / "batch.json")
        ids = ", ".join(b["id"] for b in batch["batches"])
        if _is_collected(run_dir, arm):
            rows = _read_jsonl(_arm_dir(run_dir, arm) / "results.jsonl")
            cost = sum(r["cost_usd"] for r in rows)
            valid = sum(1 for r in rows if r["valid"])
            state = f"collected {len(rows)} ({valid} valid), spent ${cost:.4f}"
        elif remote:
            if impl.provider not in clients:
                clients[impl.provider] = _client_for(arm)
            labels = [impl.state(clients[impl.provider], b["id"])[1] for b in batch["batches"]]
            state = f"submitted, awaiting collect: {', '.join(labels)}"
        else:
            state = "submitted, awaiting collect"
        lines.append(f"{head}  {state}  [{ids}] submitted {batch['submitted_at']}")
    for arm in LIVE_ARMS:
        impl = _ARM_IMPLS[arm]
        docs = sonnet if arm in SONNET_ARMS else sample
        head = f"{arm:<11} {impl.model:<18} estimate ${estimate_cost(arm, docs):.4f} (list price)"
        path = _arm_dir(run_dir, arm) / "results.jsonl"
        if not path.exists():
            lines.append(f"{head}  not run")
            continue
        rows = _read_jsonl(path)
        valid = sum(1 for r in rows if r["valid"])
        cost = sum(r["cost_usd"] for r in rows)
        lines.append(
            f"{head}  live: {len(rows)} of {len(docs)} lines ({valid} valid), spent ${cost:.4f}"
        )
    lines += [
        "",
        f"cap ${CAP_USD:.2f} | spent ${spend.collected:.4f} | "
        f"committed, not yet collected ${spend.committed:.4f} | "
        f"headroom ${CAP_USD - experiment_spend(run_dir):.4f} (arms and judge, all runs)",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.eval.extraction_bakeoff", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, run_default: str):
        sp.add_argument(
            "--run-dir",
            type=pathlib.Path,
            help=f"state directory (default: {run_default})",
        )
        sp.add_argument("--sample", type=pathlib.Path, default=SAMPLE_PATH, help="sample JSON")

    def with_sonnet(sp):
        sp.add_argument(
            "--sonnet-sample",
            type=pathlib.Path,
            default=SONNET_SAMPLE_PATH,
            help="the Sonnet subset JSON",
        )

    sub.add_parser("sample", help="pick the sample from the database and write the JSON")
    sp = sub.add_parser(
        "sample-sonnet", help="pick the 100-document Sonnet subset from the sample file"
    )
    sp.add_argument("--sample", type=pathlib.Path, default=SAMPLE_PATH, help="sample JSON to read")
    sp.add_argument(
        "--out", type=pathlib.Path, help=f"where to write (default: {SONNET_SAMPLE_PATH})"
    )
    sp = sub.add_parser("submit", help="send an arm as provider batches (spends money)")
    sp.add_argument("--arm", required=True, choices=[*ARMS, "all"])
    sp.add_argument(
        "--new-run", action="store_true", help="start a new run directory instead of the newest"
    )
    common(sp, "the newest run under backend/.bakeoff/, or a new one if there is none")
    sp = sub.add_parser("run-live", help="run a live arm through the Messages API (spends money)")
    sp.add_argument("--arm", required=True, choices=[*LIVE_ARMS, "all-live"])
    sp.add_argument(
        "--concurrency",
        type=int,
        default=LIVE_CONCURRENCY,
        help=f"calls in flight at once (default {LIVE_CONCURRENCY})",
    )
    common(sp, "the newest run under backend/.bakeoff/, or a new one if there is none")
    with_sonnet(sp)
    sp = sub.add_parser("collect", help="write results.jsonl for finished batches")
    sp.add_argument("--wait", type=float, default=0, help="poll up to this many seconds")
    sp.add_argument("--poll", type=float, default=30, help="seconds between polls")
    common(sp, "the newest run under backend/.bakeoff/")
    sp = sub.add_parser("status", help="estimates, batch state, spend")
    sp.add_argument("--remote", action="store_true", help="also ask the providers for batch state")
    common(sp, "the newest run under backend/.bakeoff/")
    with_sonnet(sp)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sample":
            sample = asyncio.run(write_sample())
            print(f"wrote {len(sample)} documents to {SAMPLE_PATH}")
            print(f"{'doc_kind':<20} {'communication_type':<20} count")
            for kind, ctype, n in strata_table(sample):
                print(f"{str(kind):<20} {str(ctype):<20} {n}")
            return 0

        if args.command == "sample-sonnet":
            subset = write_sonnet_sample(args.sample, args.out)
            print(f"wrote {len(subset)} documents to {args.out or SONNET_SAMPLE_PATH}")
            print(f"{'doc_kind':<20} {'communication_type':<20} count")
            for kind, ctype, n in strata_table(subset):
                print(f"{str(kind):<20} {str(ctype):<20} {n}")
            return 0

        if args.command == "submit":
            # The newest run by default, so arms submitted one command at a time land
            # in one run and the already-submitted guard sees them.
            run_dir = args.run_dir or (
                new_run_dir() if args.new_run else latest_run_dir() or new_run_dir()
            )
            print(f"run dir: {run_dir}")
            done = asyncio.run(submit(args.arm, run_dir, sample_path=args.sample))
            for arm, ids in done.items():
                print(f"{arm}: submitted {len(ids)} batch(es): {', '.join(ids)}")
            return 0

        run_dir = args.run_dir or latest_run_dir() or new_run_dir()
        if args.command == "run-live":
            if args.concurrency < 1:
                raise ValueError("--concurrency must be at least 1")
            print(f"run dir: {run_dir}")
            try:
                done = asyncio.run(
                    run_live(
                        args.arm,
                        run_dir,
                        sample_path=args.sample,
                        sonnet_sample_path=args.sonnet_sample,
                        concurrency=args.concurrency,
                    )
                )
            except KeyboardInterrupt:
                print(
                    "interrupted: every finished document is already in results.jsonl; "
                    "run the same command again to resume",
                    file=sys.stderr,
                )
                return 130
            for result in done.values():
                print(format_live_result(result))
            return 0
        if args.command == "collect":
            report = asyncio.run(collect(run_dir, poll_seconds=args.poll, max_wait=args.wait))
            for arm, line in report.items():
                print(f"{arm}: {line}")
            return 0
        print(
            status(
                run_dir,
                sample_path=args.sample,
                remote=args.remote,
                sonnet_sample_path=args.sonnet_sample,
            )
        )
        return 0
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
