"""Job 2: drain `pending` communications.

Runs the three stages in order and records the run in `pipeline_runs`:

    extract  download PDFs, extract text locally, flag low-yield documents
    llm      structured extraction with Claude Haiku 5.5 reading the PDF (Batch API by default, --sync for the Messages API at full price)
    embed    embeddings for semantic retrieval

Usage:
    python -m src.jobs.process --all                # all three stages, batch LLM
    python -m src.jobs.process --all --sync         # all three, synchronous LLM
    python -m src.jobs.process --stage extract
    python -m src.jobs.process --stage llm --sync --limit 20
    python -m src.jobs.process --collect <job-name> # collect a submitted batch
    python -m src.jobs.process --reextract [--vehicle N] [--cap-usd 5] [--sync] [--yes]
    python -m src.jobs.process --reextract --resume [--vehicle N] [--cap-usd 5] [--yes]
"""

import argparse
import asyncio
import json
import logging
from typing import Any, Callable, Optional

from src.communications.severity import SEVERITY_BY_TYPE, severity_for_type
from src.db import close_pool, get_pool
from src.jobs import embed as embed_stage
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage
from src.jobs.repository import vehicle_scope
from src.jobs.sync import SUMMARY_ONLY_REASON

log = logging.getLogger("process")


async def record_run(
    job: str,
    counts: dict[str, Any],
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
    ok: bool = True,
    error: Optional[str] = None,
) -> None:
    """Persist run metrics. The closeout report reads these, so a run that is
    not recorded may as well not have happened."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_runs
                (job, finished_at, ok, counts, tokens_in, tokens_out, cost_usd, error)
            VALUES ($1, now(), $2, $3::jsonb, $4, $5, $6, $7)
            """,
            job,
            ok,
            json.dumps(counts),
            tokens_in,
            tokens_out,
            cost,
            error,
        )


async def status() -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM communications)                          AS comms,
              (SELECT count(*) FROM communications WHERE status='pending')   AS pending,
              (SELECT count(*) FROM communications WHERE status='processed') AS processed,
              (SELECT count(*) FROM communications WHERE status='failed')    AS failed,
              (SELECT count(*) FROM comm_documents)                          AS docs,
              (SELECT count(*) FROM comm_documents WHERE extracted_text IS NOT NULL) AS extracted,
              (SELECT count(*) FROM comm_documents WHERE llm_summary IS NOT NULL)    AS summarised,
              (SELECT count(*) FROM comm_documents WHERE embedding IS NOT NULL)      AS embedded,
              (SELECT coalesce(sum(cost_usd),0) FROM pipeline_runs)           AS spend
            """
        )
    return dict(row)


async def run_extract(
    limit: int | None, vehicle_id: int | None = None
) -> "extract_stage.ExtractStats":
    """Extract stage. `vehicle_id` restricts it to one tracked vehicle."""
    await extract_stage.mark_summary_only_communications(vehicle_id)
    stats = await extract_stage.extract_pending(limit=limit, vehicle_id=vehicle_id)
    await record_run(
        "extract",
        {
            "downloaded": stats.downloaded,
            "from_cache": stats.from_cache,
            "extracted": stats.extracted,
            "flagged_vision": stats.flagged_vision,
            "failed": stats.failed,
            "vehicle": vehicle_id,
        },
        ok=stats.failed == 0,
        error="; ".join(stats.errors[:5]) or None,
    )
    return stats


async def run_llm(
    limit: int | None, sync: bool, vehicle_id: int | None = None
) -> Any:
    """LLM stage. Returns the stage stats, or None when the batch path had nothing to submit."""
    if sync:
        stats = await llm_stage.run_sync(limit=limit, vehicle_id=vehicle_id)
        await record_run(
            "llm",
            {
                "processed": stats.processed,
                "failed": stats.failed,
                "retried": stats.retried,
                "mode": "sync",
            },
            stats.tokens_in,
            stats.tokens_out,
            stats.cost_usd,
            ok=stats.failed == 0,
            error="; ".join(stats.errors[:5]) or None,
        )
        return stats

    # A batch from an earlier run that outlived its wait is paid for whether or not it is
    # collected. Store its results first; while one is still running, send nothing new, or
    # the documents in it would be sent, and paid for, a second time.
    earlier = await open_batches()
    if earlier:
        log.info("collecting %d batch job(s) left running by an earlier run", len(earlier))
        collected = await _collect_tracked(earlier)
        if getattr(collected, "unfinished", None):
            log.warning("an earlier batch is still running: no new documents sent this run")
            return collected

    names = await llm_stage.submit_batch(limit=limit, vehicle_id=vehicle_id)
    if names:
        await track_batches(names)
        log.info("submitted %d batch job(s); collecting", len(names))
        return await _collect_tracked(names)
    return None


async def open_batches() -> list[str]:
    """Batch jobs submitted whose results are not stored yet, oldest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT batch_id FROM llm_batches WHERE collected_at IS NULL ORDER BY submitted_at"
        )
    return [r["batch_id"] for r in rows]


async def track_batches(batch_ids: list[str]) -> None:
    """Remember submitted batch jobs until their results are stored."""
    if not batch_ids:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO llm_batches (batch_id) VALUES ($1) ON CONFLICT (batch_id) DO NOTHING",
            [(b,) for b in batch_ids],
        )


async def mark_batches_collected(batch_ids: list[str]) -> None:
    """The results of these batch jobs are stored; they are no longer out."""
    if not batch_ids:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE llm_batches SET collected_at = now() "
            "WHERE batch_id = ANY($1::text[]) AND collected_at IS NULL",
            batch_ids,
        )


async def _collect_tracked(batch_ids: list[str]) -> Any:
    """Collect batch jobs, record the run, and mark the finished ones as collected.

    Returns the stage stats; ids in `stats.unfinished` are still running at Anthropic and
    stay tracked for the next run.
    """
    stats = await llm_stage.collect_batch(batch_ids)
    unfinished = list(getattr(stats, "unfinished", None) or [])
    finished = [b for b in batch_ids if b not in unfinished]
    if finished:
        await mark_batches_collected(finished)
    await record_run(
        "llm",
        {"processed": stats.processed, "failed": stats.failed, "mode": "batch", "jobs": batch_ids},
        stats.tokens_in,
        stats.tokens_out,
        stats.cost_usd,
        ok=stats.failed == 0,
        error="; ".join(stats.errors[:5]) or None,
    )
    return stats


async def run_embed(limit: int | None, vehicle_id: int | None = None) -> Any:
    stats = await embed_stage.embed_pending(limit=limit, vehicle_id=vehicle_id)
    await record_run(
        "embed",
        {"embedded": stats.embedded, "failed": stats.failed, "vehicle": vehicle_id},
        stats.tokens_in,
        0,
        stats.cost_usd,
        ok=stats.failed == 0,
        error="; ".join(stats.errors[:5]) or None,
    )
    return stats


# Documents per LLM slice. The cap is checked between slices, so this bounds how far a run can
# overshoot it (about $0.08 on the Batch path).
REEXTRACT_SLICE = 50
CAP_EXIT_CODE = 3
# Anthropic's batch window is 24 hours; wait slightly longer so a batch that ends at the very
# end of the window is still collected.
BATCH_WAIT_SECONDS = 25 * 3600
BATCH_POLL_SECONDS = 60
_LEDGER_KINDS = "('llm_extract', 'embed')"

# A stranded run leaves communications `failed` for transient reasons; --resume re-queues these
# classes. `LLM schema violation` and `batch ` failures are deliberately not listed.
_TRANSIENT_REASON_PATTERNS = [
    "pdf unavailable:%",
    "LLM call failed:%",
    "LLM stage error:%",
    # extract._record_failure writes "document <file>: <reason>"; only network-class reasons
    # count. "pdf open failed", "pdf has zero pages" and "http 404" are permanent.
    "document %: ConnectError%",
    "document %: ConnectTimeout%",
    "document %: ReadTimeout%",
    "document %: ReadError%",
    "document %: RemoteProtocolError%",
    "document %: PoolTimeout%",
    "document %: http 5%",
    "document %: download failed%",
    "document %: empty response body%",
]

# Token shape of one document at the current model when the ledger has no history for it
# (the measured effort-high mean: 9,226 in, 1,783 out).
FALLBACK_TOKENS_PER_DOCUMENT = (9_000, 1_800)

# The reset writes these back to NULL (or empty); `symptoms`, `systems` and `components` are
# NOT NULL arrays, so they go to '{}'.
_RESET_DOCUMENT_SQL = """
    UPDATE comm_documents SET
        llm_summary = NULL, doc_kind = NULL, symptoms = '{}', systems = '{}', components = '{}',
        remedy = NULL, applicability = NULL, applicability_years = '{}',
        applicability_models = '{}', applicability_conditions = NULL, audience = NULL,
        llm_model = NULL, llm_tokens_in = NULL, llm_tokens_out = NULL, llm_cost_usd = NULL,
        llm_at = NULL, embedding = NULL, embedding_model = NULL, embedded_at = NULL
    WHERE id = ANY($1::bigint[])
"""


async def _spent_since(conn: Any, started_at: Any) -> float:
    return float(
        await conn.fetchval(
            f"SELECT coalesce(sum(cost_usd), 0) FROM usage_events "
            f"WHERE occurred_at >= $1 AND kind IN {_LEDGER_KINDS}",
            started_at,
        )
    )


async def _awaiting_llm(conn: Any, vehicle_id: int | None) -> int:
    """Documents the LLM stage would pick up now (same predicate as `llm._pending_documents`)."""
    return await conn.fetchval(
        f"""
        SELECT count(*) FROM comm_documents d
        JOIN communications c ON c.id = d.communication_id
        WHERE d.llm_summary IS NULL AND c.status = 'pending' AND d.extracted_at IS NOT NULL
          AND {vehicle_scope('$1')}
        """,
        vehicle_id,
    )


async def _per_document_estimate(conn: Any, sync: bool) -> float:
    """Expected spend per document for the path about to run, never discounted twice.

    `refresh.estimate.cost_per_document` averages every `llm_extract` row, Batch rows included
    at their already-halved price, so halving it again under-quotes a Batch run by half and a
    change of model or effort by whatever the history was. Here the mean is taken over the
    current model's rows on the same path (live or batch); failing that, the model's token
    averages are priced at list for that path; failing that, a fixed token shape is. The embed
    share is the ledger's own mean per unit and is never discounted.
    """
    from src.config import get_settings
    from src.usage import pricing

    model = get_settings().extraction_model
    path = "live" if sync else "batch"
    row = await conn.fetchrow(
        """
        SELECT
          (SELECT sum(cost_usd) / nullif(count(DISTINCT document_id), 0)
             FROM usage_events
            WHERE kind = 'llm_extract' AND model = $1 AND document_id IS NOT NULL
              AND meta->>'path' = $2)                                        AS same_path,
          (SELECT avg(tokens_in) FROM usage_events
            WHERE kind = 'llm_extract' AND model = $1 AND document_id IS NOT NULL) AS tokens_in,
          (SELECT avg(tokens_out) FROM usage_events
            WHERE kind = 'llm_extract' AND model = $1 AND document_id IS NOT NULL) AS tokens_out,
          (SELECT sum(cost_usd) / nullif(sum(unit_count), 0)
             FROM usage_events WHERE kind = 'embed')                          AS embed_per_unit
        """,
        model,
        path,
    )
    if row["same_path"] is not None:
        llm = float(row["same_path"])
    elif row["tokens_in"] is not None:
        llm = pricing.cost_of(
            model, int(row["tokens_in"]), int(row["tokens_out"] or 0), batch=not sync
        )
    else:
        tin, tout = FALLBACK_TOKENS_PER_DOCUMENT
        llm = pricing.cost_of(model, tin, tout, batch=not sync)
    embed = float(row["embed_per_unit"]) if row["embed_per_unit"] is not None else 0.0
    return llm + embed


async def _reextract_plan(
    vehicle_id: int | None, cap_usd: float, resume: bool = False, sync: bool = True
) -> dict[str, Any]:
    """What a reset would touch and what re-reading it should cost (a resume resets nothing)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
              (SELECT count(*) FROM communications c
                WHERE c.status = 'processed' AND c.status_reason IS DISTINCT FROM $2
                  AND {vehicle_scope('$1')}) AS communications,
              (SELECT count(*) FROM comm_documents d JOIN communications c
                    ON c.id = d.communication_id
                WHERE d.llm_summary IS NOT NULL AND c.status = 'processed'
                  AND c.status_reason IS DISTINCT FROM $2
                  AND {vehicle_scope('$1')}) AS documents,
              (SELECT count(*) FROM comm_documents d JOIN communications c
                    ON c.id = d.communication_id
                WHERE d.llm_summary IS NULL AND c.status = 'pending'
                  AND {vehicle_scope('$1')}) AS pending_documents,
              (SELECT count(*) FROM communications c
                WHERE c.status = 'failed' AND c.status_reason LIKE ANY($3::text[])
                  AND {vehicle_scope('$1')}) AS requeue_communications,
              (SELECT count(*) FROM comm_documents d JOIN communications c
                    ON c.id = d.communication_id
                WHERE d.llm_summary IS NULL AND c.status = 'failed'
                  AND c.status_reason LIKE ANY($3::text[])
                  AND {vehicle_scope('$1')}) AS requeue_documents
            """,
            vehicle_id,
            SUMMARY_ONLY_REASON,
            _TRANSIENT_REASON_PATTERNS,
        )
        per_doc = await _per_document_estimate(conn, sync)
    plan = dict(row)
    plan["resume"] = resume
    if resume:
        plan["communications"] = 0
        plan["documents"] = 0
    plan["per_document_usd"] = per_doc
    plan["estimated_cost_usd"] = per_doc * (
        plan["documents"]
        + plan["pending_documents"]
        + (plan["requeue_documents"] if resume else 0)
    )
    plan["cap_usd"] = cap_usd
    return plan


def _print_plan(plan: dict[str, Any], vehicle_id: int | None) -> None:
    scope = f"vehicle {vehicle_id}" if vehicle_id is not None else "all vehicles"
    if plan.get("resume"):
        print(f"Re-extract resume plan ({scope}; no reset):")
        print(f"  network-failed to re-queue {plan['requeue_communications']}")
        print(f"  pending documents         {plan['pending_documents']}")
    else:
        print(f"Re-extract plan ({scope}):")
        print(f"  processed communications  {plan['communications']}")
        print(f"  documents to reset        {plan['documents']}")
        print(f"  pending documents         {plan['pending_documents']} (ride along)")
    print(f"  estimated cost            ${plan['estimated_cost_usd']:.2f}")
    print(f"  spend cap                 ${plan['cap_usd']:.2f}")


async def _reset_for_reextract(vehicle_id: int | None) -> dict[str, int]:
    """Clear the LLM fields and embeddings of processed documents and send their
    communications back to `pending`, in one transaction. Summary-only rows are left alone."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            docs = await conn.fetch(
                f"""
                SELECT d.id, d.communication_id, c.communication_type
                FROM comm_documents d JOIN communications c ON c.id = d.communication_id
                WHERE d.llm_summary IS NOT NULL AND c.status = 'processed'
                  AND c.status_reason IS DISTINCT FROM $2
                  AND {vehicle_scope('$1')}
                FOR UPDATE OF d, c
                """,
                vehicle_id,
                SUMMARY_ONLY_REASON,
            )
            await conn.execute(_RESET_DOCUMENT_SQL, [r["id"] for r in docs])
            comms = {r["communication_id"]: r["communication_type"] for r in docs}
            # Severity returns to the type mapping with source 'type', so the classifier
            # (promote_completed_communications) recomputes catch-all types from the new read.
            by_severity: dict[str, list[int]] = {}
            for comm_id, ctype in comms.items():
                by_severity.setdefault(severity_for_type(ctype), []).append(comm_id)
            for severity, ids in by_severity.items():
                await conn.execute(
                    """
                    UPDATE communications SET status = 'pending', status_reason = NULL,
                        processed_at = NULL, attempts = 0,
                        severity = $2, severity_source = 'type'
                    WHERE id = ANY($1::bigint[])
                    """,
                    ids,
                    severity,
                )
    return {"communications": len(comms), "documents": len(docs)}


async def _requeue_transient_failures(vehicle_id: int | None) -> int:
    """Return communications that failed on a network-class error to `pending`."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            UPDATE communications c SET status = 'pending', status_reason = NULL, attempts = 0
            WHERE c.status = 'failed' AND c.status_reason LIKE ANY($2::text[])
              AND {vehicle_scope('$1')}
            RETURNING c.id
            """,
            vehicle_id,
            _TRANSIENT_REASON_PATTERNS,
        )
    return len(rows)


async def _unresolved_batches(conn: Any) -> list[str]:
    """Batch ids submitted by an earlier run and never collected.

    `_batch_llm_once` records the ids at submission (mode `batch-submitted`) and again when the
    batch is collected (mode `batch`). An id in the first set and not the second is still
    Anthropic's to finish; submitting the same pending documents again would pay twice.
    """
    rows = await conn.fetch(
        "SELECT counts->>'mode' AS mode, counts->'jobs' AS jobs FROM pipeline_runs "
        "WHERE job = 'llm' AND counts->>'mode' IN ('batch-submitted', 'batch')"
    )
    submitted: list[str] = []
    collected: set[str] = set()
    for r in rows:
        jobs = r["jobs"]
        if isinstance(jobs, str):
            jobs = json.loads(jobs)
        for job_id in jobs or []:
            if r["mode"] == "batch-submitted":
                submitted.append(job_id)
            else:
                collected.add(job_id)
    return [j for j in submitted if j not in collected]


async def _unfinished_counts(conn: Any, vehicle_id: int | None) -> dict[str, int]:
    """Post-run state of the in-scope communications (summary-only rows excluded)."""
    scope = (
        "c.status IN ('processed', 'pending', 'failed') "
        f"AND c.status_reason IS DISTINCT FROM $2 AND {vehicle_scope('$1')}"
    )
    row = await conn.fetchrow(
        f"""
        SELECT
          (SELECT count(*) FROM communications c
            WHERE c.status = 'failed' AND {scope}) AS failed_communications,
          (SELECT count(*) FROM comm_documents d JOIN communications c
                ON c.id = d.communication_id
            WHERE d.llm_summary IS NULL AND {scope}) AS without_summary,
          (SELECT count(*) FROM comm_documents d JOIN communications c
                ON c.id = d.communication_id
            WHERE d.llm_summary IS NOT NULL AND d.embedding IS NULL AND {scope}) AS unembedded
        """,
        vehicle_id,
        SUMMARY_ONLY_REASON,
    )
    return {k: int(v) for k, v in dict(row).items()}


async def _batch_llm_once(
    vehicle_id: int | None, cap_usd: float, started_at: Any, per_doc: float
) -> tuple[str | None, int]:
    """Batch path: one estimate check, ONE submission of everything pending, one collection.

    Slicing would queue each slice behind Anthropic's batch queue in turn and collect_batch
    abandons a batch after its wait, so the whole backlog goes up at once. Returns the error
    (None when done, "cap reached" or "no progress") and the number of failed documents.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        remaining = await _awaiting_llm(conn, vehicle_id)
        if remaining == 0:
            return None, 0
        estimate = per_doc * remaining  # per_doc is already the Batch-path figure
        if estimate >= cap_usd:
            log.warning(
                "cap reached: estimate $%.2f for %d documents is not under the $%.2f cap; "
                "nothing submitted",
                estimate,
                remaining,
                cap_usd,
            )
            return "cap reached", 0

    ids = await llm_stage.submit_batch(limit=None, vehicle_id=vehicle_id)
    failed = 0
    if ids:
        # Recorded before the wait so a lost session (or --resume) can see the batch is out,
        # and tracked so the nightly run collects it instead of sending the documents again.
        await track_batches(ids)
        await record_run("llm", {"mode": "batch-submitted", "jobs": ids})
        log.info(
            "SUBMITTED %d batch job(s): %s -- if this session is lost, finish with: "
            "python -m src.jobs.process --collect %s",
            len(ids),
            ", ".join(ids),
            " --collect ".join(ids),
        )
        stats = await llm_stage.collect_batch(
            ids, poll_seconds=BATCH_POLL_SECONDS, max_wait=BATCH_WAIT_SECONDS
        )
        failed = stats.failed
        still_out = getattr(stats, "unfinished", None) or []
        await mark_batches_collected([b for b in ids if b not in still_out])
        await record_run(
            "llm",
            {"processed": stats.processed, "failed": stats.failed, "mode": "batch", "jobs": ids},
            stats.tokens_in,
            stats.tokens_out,
            stats.cost_usd,
            ok=stats.failed == 0,
            error="; ".join(stats.errors[:5]) or None,
        )
    async with pool.acquire() as conn:
        spent = await _spent_since(conn, started_at)
        left = await _awaiting_llm(conn, vehicle_id)
    if spent >= cap_usd:
        log.warning("cap reached: $%.2f of $%.2f after the batch", spent, cap_usd)
        return "cap reached", failed
    if left >= remaining:
        log.warning("batch made no progress; %d documents left pending", remaining)
        return "no progress", failed
    return None, failed


async def reextract(
    vehicle_id: int | None,
    cap_usd: float,
    sync: bool,
    limit: int | None,
    confirm: Callable[[], bool] | None = None,
    resume: bool = False,
) -> int:
    """Re-read a corpus under the current model and schema, refusing to overspend.

    Returns the process exit code: 0 clean, 1 declined at the prompt or any failure or
    stranded rows (a stage reported failures, or in-scope communications are left `failed`,
    without a summary or without an embedding), 3 cap reached. `confirm` is asked after the plan
    prints; None proceeds (the CLI passes a stdin prompt unless --yes).

    `resume` skips the reset and finishes a stranded run over whatever is `pending`, first
    re-queueing communications that failed on a network-class error.
    """
    assert SEVERITY_BY_TYPE  # the reset leans on the type mapping; fail loudly if it is empty
    plan = await _reextract_plan(vehicle_id, cap_usd, resume, sync)
    _print_plan(plan, vehicle_id)
    if plan["estimated_cost_usd"] >= cap_usd:
        # Refused here, before the confirm and the reset: the old order wiped every summary
        # and then found the cap, leaving the corpus empty with nothing submitted.
        log.warning(
            "cap reached: estimate $%.2f is not under the $%.2f cap; nothing was changed",
            plan["estimated_cost_usd"],
            cap_usd,
        )
        await record_run(
            "reextract",
            {"vehicle": vehicle_id, "cap_usd": cap_usd, "estimate_usd": plan["estimated_cost_usd"]},
            ok=False,
            error="cap reached",
        )
        return CAP_EXIT_CODE
    if not sync:
        pool = await get_pool()
        async with pool.acquire() as conn:
            unresolved = await _unresolved_batches(conn)
        if unresolved:
            print(
                "A batch from an earlier run has not been collected; collect it first so its "
                "documents are not paid for twice:\n  python -m src.jobs.process --collect "
                + " --collect ".join(unresolved)
            )
            return 1
    if confirm is not None and not confirm():
        print("Aborted; nothing was changed.")
        return 1

    if resume:
        requeued = await _requeue_transient_failures(vehicle_id)
        print(f"Re-queued {requeued} communication(s) that failed on a network error.")
        reset = {"communications": 0, "documents": 0, "requeued": requeued}
    else:
        reset = await _reset_for_reextract(vehicle_id)
        await record_run("reextract-reset", {**reset, "vehicle": vehicle_id})
    pool = await get_pool()
    async with pool.acquire() as conn:
        started_at = await conn.fetchval("SELECT now()")

    stage_failed = {"extract": 0, "llm": 0, "embed": 0}
    extract_stats = await run_extract(limit, vehicle_id)
    stage_failed["extract"] += int(getattr(extract_stats, "failed", 0) or 0)

    error: Optional[str] = None
    if not sync:
        error, llm_failed = await _batch_llm_once(
            vehicle_id, cap_usd, started_at, plan["per_document_usd"]
        )
        stage_failed["llm"] += llm_failed
    else:
        async with pool.acquire() as conn:
            while True:
                remaining = await _awaiting_llm(conn, vehicle_id)
                if remaining == 0:
                    break
                spent = await _spent_since(conn, started_at)
                if spent >= cap_usd:
                    error = "cap reached"
                    log.warning(
                        "cap reached: $%.2f of $%.2f; %d documents left pending",
                        spent,
                        cap_usd,
                        remaining,
                    )
                    break
                llm_stats = await run_llm(REEXTRACT_SLICE, sync, vehicle_id)
                stage_failed["llm"] += int(getattr(llm_stats, "failed", 0) or 0)
                if await _awaiting_llm(conn, vehicle_id) >= remaining:
                    error = "no progress"  # failures or an empty batch; looping would never end
                    log.warning("llm slice made no progress; %d documents left pending", remaining)
                    break

    if error is None:
        embed_stats = await run_embed(None, vehicle_id)
        stage_failed["embed"] += int(getattr(embed_stats, "failed", 0) or 0)
        await extract_stage.prune_archive()

    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            f"""
            SELECT coalesce(sum(tokens_in), 0) AS tokens_in,
                   coalesce(sum(tokens_out), 0) AS tokens_out,
                   coalesce(sum(cost_usd), 0)   AS cost_usd
            FROM usage_events WHERE occurred_at >= $1 AND kind IN {_LEDGER_KINDS}
            """,
            started_at,
        )
        left = await _awaiting_llm(conn, vehicle_id)
        unfinished = await _unfinished_counts(conn, vehicle_id)

    # A network drop strands rows instead of raising, so success is judged on the end state,
    # not on the stages having returned.
    problems: list[str] = []
    if error not in (None, "cap reached"):
        problems.append(error)
    for stage, count in stage_failed.items():
        if count:
            problems.append(f"{stage} failed={count}")
    if unfinished["failed_communications"]:
        problems.append(f"{unfinished['failed_communications']} communications failed")
    if unfinished["without_summary"]:
        problems.append(f"{unfinished['without_summary']} documents without a summary")
    if unfinished["unembedded"]:
        problems.append(f"{unfinished['unembedded']} documents without an embedding")
    if error != "cap reached" and problems:
        error = "; ".join(problems)
        log.error("re-extract finished with failures: %s", error)

    await record_run(
        "reextract",
        {
            **reset,
            "left_pending": left,
            "vehicle": vehicle_id,
            "cap_usd": cap_usd,
            "failed": unfinished["failed_communications"],
            "without_summary": unfinished["without_summary"],
            "unembedded": unfinished["unembedded"],
            "stage_failures": stage_failed,
        },
        int(totals["tokens_in"]),
        int(totals["tokens_out"]),
        float(totals["cost_usd"]),
        ok=error is None,
        error=error,
    )
    for k, v in (await status()).items():
        log.info("  %-12s %s", k, v)
    return CAP_EXIT_CODE if error == "cap reached" else (0 if error is None else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain pending communications.")
    parser.add_argument("--all", action="store_true", help="run extract, llm, and embed in order")
    parser.add_argument("--stage", choices=["extract", "llm", "embed"], help="run a single stage")
    parser.add_argument("--collect", action="append", help="collect a submitted batch job by name")
    parser.add_argument("--status", action="store_true", help="print pipeline counts and exit")
    parser.add_argument(
        "--sync", action="store_true", help="use the synchronous LLM API (full price)"
    )
    parser.add_argument("--limit", type=int, help="cap documents processed")
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="reset processed documents and re-read them (extract, llm, embed) under --cap-usd",
    )
    parser.add_argument("--vehicle", type=int, help="with --reextract, one vehicles.id only")
    parser.add_argument(
        "--cap-usd", type=float, default=5.0, help="with --reextract, stop at this spend (5.0)"
    )
    parser.add_argument("--yes", action="store_true", help="with --reextract, skip the prompt")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "with --reextract, skip the reset and finish a stranded run over whatever is "
            "pending (also re-queues communications that failed on a network error)"
        ),
    )
    parser.add_argument(
        "--prune-archive",
        action="store_true",
        help="delete archived PDFs for documents already summarised",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --prune-archive, only report what would go"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.resume and not args.reextract:
        parser.error("--resume only applies with --reextract")
    if args.reextract and args.limit:
        # The reset always covers the whole scope; --limit would only reach the extract
        # stage, so `--reextract --limit 5` would still clear and re-read every document.
        parser.error("--limit does not apply to --reextract; narrow it with --vehicle")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs a line per PDF download at INFO, which buries the run summary.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def _main() -> int:
        try:
            if args.status:
                for k, v in (await status()).items():
                    print(f"  {k:12s} {v}")
                return 0
            if args.reextract:
                confirm = (
                    None
                    if args.yes
                    else (lambda: input("Proceed? [y/N] ").strip().lower() in ("y", "yes"))
                )
                return await reextract(
                    args.vehicle, args.cap_usd, args.sync, args.limit, confirm, args.resume
                )
            if args.collect:
                stats = await llm_stage.collect_batch(
                    args.collect, poll_seconds=BATCH_POLL_SECONDS, max_wait=BATCH_WAIT_SECONDS
                )
                await mark_batches_collected(
                    [b for b in args.collect if b not in stats.unfinished]
                )
                if stats.unfinished:
                    log.error(
                        "not collected, still running at Anthropic: %s; run --collect again",
                        ", ".join(stats.unfinished),
                    )
                    return 1
                return 0
            if args.prune_archive:
                removed, freed = await extract_stage.prune_archive(dry_run=args.dry_run)
                verb = "would remove" if args.dry_run else "removed"
                print(f"  {verb} {removed} pdf(s), {freed / 1_048_576:.1f} MB")
                return 0
            if args.stage == "extract":
                await run_extract(args.limit)
            elif args.stage == "llm":
                await run_llm(args.limit, args.sync)
            elif args.stage == "embed":
                await run_embed(args.limit)
            elif args.all:
                await run_extract(args.limit)
                await run_llm(args.limit, args.sync)
                await run_embed(args.limit)
            else:
                parser.error("pass --all, --stage, --collect, or --status")

            log.info("pipeline status:")
            for k, v in (await status()).items():
                log.info("  %-12s %s", k, v)
            return 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
