"""Job 2: drain `pending` communications.

Runs the three stages in order and records the run in `pipeline_runs`:

    extract  download PDFs, extract text locally, flag low-yield documents
    llm      structured extraction (Batch API by default, --sync for full price)
    embed    embeddings for semantic retrieval

Usage:
    python -m src.jobs.process --all                # all three stages, batch LLM
    python -m src.jobs.process --all --sync         # all three, synchronous LLM
    python -m src.jobs.process --stage extract
    python -m src.jobs.process --stage llm --sync --limit 20
    python -m src.jobs.process --collect <job-name> # collect a submitted batch
"""

import argparse
import asyncio
import json
import logging
from typing import Any, Optional

from src.db import close_pool, get_pool
from src.jobs import embed as embed_stage
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage

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


async def run_extract(limit: int | None) -> None:
    await extract_stage.mark_documentless_communications()
    stats = await extract_stage.extract_pending(limit=limit)
    await record_run(
        "extract",
        {
            "downloaded": stats.downloaded,
            "from_cache": stats.from_cache,
            "extracted": stats.extracted,
            "flagged_vision": stats.flagged_vision,
            "failed": stats.failed,
        },
        ok=stats.failed == 0,
        error="; ".join(stats.errors[:5]) or None,
    )


async def run_llm(limit: int | None, sync: bool) -> None:
    if sync:
        stats = await llm_stage.run_sync(limit=limit)
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

    names = await llm_stage.submit_batch(limit=limit)
    if not names:
        return
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


async def run_embed(limit: int | None) -> None:
    stats = await embed_stage.embed_pending(limit=limit)
    await record_run(
        "embed",
        {"embedded": stats.embedded, "failed": stats.failed},
        stats.tokens_in,
        0,
        stats.cost_usd,
        ok=stats.failed == 0,
        error="; ".join(stats.errors[:5]) or None,
    )


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
            if args.collect:
                await llm_stage.collect_batch(args.collect)
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
