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


async def run_llm(limit: int | None, sync: bool, vehicle_id: int | None = None) -> None:
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
        return

    names = await llm_stage.submit_batch(limit=limit, vehicle_id=vehicle_id)
    if names:
        log.info("submitted %d batch job(s); collecting", len(names))
        stats = await llm_stage.collect_batch(names)
        await record_run(
            "llm",
            {"processed": stats.processed, "failed": stats.failed, "mode": "batch", "jobs": names},
            stats.tokens_in,
            stats.tokens_out,
            stats.cost_usd,
            ok=stats.failed == 0,
            error="; ".join(stats.errors[:5]) or None,
        )


async def run_embed(limit: int | None, vehicle_id: int | None = None) -> None:
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


# Documents per LLM slice. The cap is checked between slices, so this bounds how far a run can
# overshoot it (about $0.08 on the Batch path).
REEXTRACT_SLICE = 50
CAP_EXIT_CODE = 3
_LEDGER_KINDS = "('llm_extract', 'embed')"

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


async def _reextract_plan(vehicle_id: int | None, cap_usd: float) -> dict[str, Any]:
    """What a reset would touch and what re-reading it should cost."""
    from src.refresh.estimate import cost_per_document

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
                  AND {vehicle_scope('$1')}) AS pending_documents
            """,
            vehicle_id,
            SUMMARY_ONLY_REASON,
        )
    plan = dict(row)
    per_doc = await cost_per_document()
    plan["estimated_cost_usd"] = per_doc * (plan["documents"] + plan["pending_documents"])
    plan["cap_usd"] = cap_usd
    return plan


def _print_plan(plan: dict[str, Any], vehicle_id: int | None) -> None:
    scope = f"vehicle {vehicle_id}" if vehicle_id is not None else "all vehicles"
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


async def reextract(
    vehicle_id: int | None,
    cap_usd: float,
    sync: bool,
    limit: int | None,
    confirm: Callable[[], bool] | None = None,
) -> int:
    """Re-read a corpus under the current model and schema, refusing to overspend.

    Returns the process exit code: 0 done, 1 declined at the prompt, 3 cap reached. `confirm`
    is asked after the plan prints; None proceeds (the CLI passes a stdin prompt unless --yes).
    """
    assert SEVERITY_BY_TYPE  # the reset leans on the type mapping; fail loudly if it is empty
    plan = await _reextract_plan(vehicle_id, cap_usd)
    _print_plan(plan, vehicle_id)
    if confirm is not None and not confirm():
        print("Aborted; nothing was changed.")
        return 1

    reset = await _reset_for_reextract(vehicle_id)
    await record_run("reextract-reset", {**reset, "vehicle": vehicle_id})
    pool = await get_pool()
    async with pool.acquire() as conn:
        started_at = await conn.fetchval("SELECT now()")

    await run_extract(limit, vehicle_id)

    error: Optional[str] = None
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
            await run_llm(REEXTRACT_SLICE, sync, vehicle_id)
            if await _awaiting_llm(conn, vehicle_id) >= remaining:
                error = "no progress"  # failures or an empty batch; looping would never end
                log.warning("llm slice made no progress; %d documents left pending", remaining)
                break

    if error is None:
        await run_embed(None, vehicle_id)
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
    await record_run(
        "reextract",
        {**reset, "left_pending": left, "vehicle": vehicle_id, "cap_usd": cap_usd},
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
        "--prune-archive",
        action="store_true",
        help="delete archived PDFs for documents already summarised",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --prune-archive, only report what would go"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

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
                return await reextract(args.vehicle, args.cap_usd, args.sync, args.limit, confirm)
            if args.collect:
                await llm_stage.collect_batch(args.collect)
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
