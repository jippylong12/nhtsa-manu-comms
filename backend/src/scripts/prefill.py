"""Prefill the landing page: track the cars people drive and read every bulletin for them.

For each entry of a list file, create the vehicle (idempotent: an existing one is reused),
scan it against the flat-file index, sync its bulletins, then run the process stages through
the Batch API, all under a spend cap. The stages are scoped to the listed vehicles, so
documents that belong to other tracked vehicles are never swept up and billed.

The list is a JSON array of ``{"year": 2020, "make": "FORD", "model": "F-150"}`` objects
(models as a person types them; ``create_vehicle`` normalises). The default is
``src/seed/top50.json``: ten nameplates x model years 2020-2024. JSON has no comments, so
this is where the file is documented; edit it to change the list. Cost is measured on the
ledger (``usage_events``) from a watermark taken just before the first write.

Usage:
    python -m src.scripts.prefill --dry-run                      # default: print the plan
    python -m src.scripts.prefill --only 4 --dry-run             # first 4 entries
    python -m src.scripts.prefill --apply --cap-usd 15 --yes     # write, sync, process
    python -m src.scripts.prefill --list my.json --apply         # another list; asks first

Exit codes: 0 done, 1 failure or declined, 3 cap reached. A run that stops (cap, crash,
queue wait) is finished by running the same command again: sync skips known bulletins, and
the stages only pick up documents not yet processed.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from src.communications import flatfile
from src.communications.flatfile import normalize_model
from src.db import close_pool, get_pool
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage
from src.jobs import process
from src.jobs import sync as sync_job
from src.refresh import estimate
from src.vehicles.service import VehicleService

log = logging.getLogger("prefill")

DEFAULT_LIST = Path(__file__).resolve().parents[1] / "seed" / "top50.json"
DEFAULT_CAP_USD = 15.0
CAP_EXIT_CODE = process.CAP_EXIT_CODE

# The runs are meant to be recorded as `prefill-sync` and `prefill-process`, but the
# pipeline_runs.job CHECK (migrations/0012) does not list them and a migration is outside this
# issue's files. Until one widens it, they are recorded under the existing names `sync` and
# `reextract`, labelled by counts.run. Widen the CHECK, then set these to the labels.
JOB_SYNC, RUN_SYNC = "sync", "prefill-sync"
JOB_PROCESS, RUN_PROCESS = "reextract", "prefill-process"


def load_list(path: str | Path, only: int | None = None) -> list[dict[str, Any]]:
    """The entries of a list file (the first ``only`` of them)."""
    entries = json.loads(Path(path).read_text())
    for e in entries:
        if not {"year", "make", "model"} <= set(e):
            raise ValueError(f"list entry needs year, make and model: {e!r}")
    return entries[:only] if only else entries


def _identity(entry: dict[str, Any]) -> tuple[int, str, str]:
    return int(entry["year"]), str(entry["make"]).strip().upper(), normalize_model(entry["model"])


async def _existing_id(conn: Any, identity: tuple[int, str, str]) -> int | None:
    return await conn.fetchval(
        "SELECT id FROM vehicles WHERE year = $1 AND make = $2 AND model = $3 "
        "ORDER BY active DESC, id LIMIT 1",
        *identity,
    )


async def _bulletins_listed(conn: Any, identity: tuple[int, str, str]) -> int:
    """Bulletins the flat-file index lists for the identity, under every spelling the
    picker folds into it (the names the scan matches). A bulletin listed in two windows
    counts in each, so this errs high."""
    year, make, model = identity
    aliases = await flatfile.model_aliases(conn, year, make, model)
    return int(
        await conn.fetchval(
            "SELECT coalesce(sum(bulletins), 0) FROM comm_vocab "
            "WHERE model_year = $1 AND make = $2 AND model = ANY($3::text[])",
            year,
            make,
            aliases,
        )
    )


async def dry_run(entries: list[dict[str, Any]], cap_usd: float) -> int:
    """Print what an apply would create and what it would cost. Writes nothing."""
    per_document = await estimate.cost_per_document()
    ratio = await estimate.documents_per_bulletin()
    pool = await get_pool()
    rows = []
    async with pool.acquire() as conn:
        for e in entries:
            identity = _identity(e)
            rows.append(
                (identity, await _existing_id(conn, identity), await _bulletins_listed(conn, identity))
            )

    print(f"Prefill plan (dry run): {len(rows)} identities")
    for (year, make, model), existing, bulletins in rows:
        state = f"exists (id {existing})" if existing else "new"
        print(f"  {year} {make} {model:<18} {state:<16} {bulletins:>5} bulletins")
    total = sum(b for _, _, b in rows)
    documents = round(total * ratio)
    cost = documents * per_document
    async with pool.acquire() as conn:
        batch_per_doc = await process._per_document_estimate(conn, sync=False)
    batch_cost = documents * batch_per_doc
    print(
        f"Estimated: {total} bulletins, about {documents} documents; about ${cost:.2f} at the "
        f"ledger mean (${per_document:.4f} per document, all history), about ${batch_cost:.2f} "
        f"at the current model's Batch rate (${batch_per_doc:.4f} per document, the figure the "
        f"cap gate uses); cap ${cap_usd:.2f}."
    )
    if batch_cost >= cap_usd:
        print("The Batch estimate is not under the cap: an apply would stop at the cap.")
    print("Nothing was written. Re-run with --apply to create, sync and process.")
    return 0


async def _spent_since(watermark: Any) -> float:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await process._spent_since(conn, watermark)


async def _ledger_totals(watermark: Any) -> tuple[int, int, float]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT coalesce(sum(tokens_in), 0) AS tin, coalesce(sum(tokens_out), 0) AS tout, "
            f"coalesce(sum(cost_usd), 0) AS cost FROM usage_events "
            f"WHERE occurred_at >= $1 AND kind IN {process._LEDGER_KINDS}",
            watermark,
        )
    return int(row["tin"]), int(row["tout"]), float(row["cost"])


async def apply(
    entries: list[dict[str, Any]], cap_usd: float, confirm: Callable[[], bool] | None
) -> int:
    """Create, scan, sync and process the listed vehicles under ``cap_usd``."""
    if confirm is not None and not confirm():
        print("Aborted; nothing was changed.")
        return 1
    pool = await get_pool()
    async with pool.acquire() as conn:
        unresolved = await process._unresolved_batches(conn)
        per_doc = await process._per_document_estimate(conn, sync=False)
        watermark = await conn.fetchval("SELECT now()")
    print(f"Ledger watermark: {watermark.isoformat()}")
    if unresolved:
        # A run that was cut off while a batch was out: finish that batch first, so its
        # documents are collected (and paid for) once, then carry on with the same list.
        print("Collecting a batch left out by an earlier run: " + ", ".join(unresolved))
        collected = await llm_stage.collect_batch(
            unresolved, poll_seconds=process.BATCH_POLL_SECONDS, max_wait=process.BATCH_WAIT_SECONDS
        )
        if collected.unfinished:
            print("Still running at Anthropic: " + ", ".join(collected.unfinished) + "; run again.")
            return 1
        await process.record_run(
            "llm",
            {"mode": "batch", "jobs": unresolved, "processed": collected.processed},
            collected.tokens_in,
            collected.tokens_out,
            collected.cost_usd,
        )

    # 1. Vehicles: idempotent, so an existing one is reused and nothing is duplicated.
    ids: list[int] = []
    created = existing = 0
    for e in entries:
        identity = _identity(e)
        async with pool.acquire() as conn:
            was_there = await _existing_id(conn, identity) is not None
        vehicle = await VehicleService.create_vehicle(str(e["year"]), e["make"], e["model"])
        ids.append(int(vehicle["vehicle_id"]))
        created, existing = created + (not was_there), existing + was_there
    print(f"Vehicles: {created} created, {existing} existing")

    # 2. Scan each against the flat-file index (the app does this in the background on add;
    #    already-current scans cost only a HEAD request), then sync their bulletins.
    #    One call for every listed vehicle: each window is read once, not once per vehicle.
    async with pool.acquire() as conn:
        await flatfile.refresh_index(conn, vehicles=ids)
    stats = await sync_job.run(vehicle_ids=ids, limit=None)
    sync_counts = {
        k: getattr(stats, k, 0)
        for k in ("found", "new_comms", "new_docs", "new_links", "fetch_failures")
    }
    await process.record_run(
        JOB_SYNC,
        {"run": RUN_SYNC, "created": created, "existing": existing, "vehicle_ids": ids, **sync_counts},
        ok=not getattr(stats, "errors", None),
        error="; ".join(getattr(stats, "errors", [])[:5]) or None,
    )
    print(f"Sync: {stats.summary() if hasattr(stats, 'summary') else sync_counts}")

    # 3. The stages, per vehicle (the only scope the stages take), cap-checked between them.
    failed = {"extract": 0, "llm": 0, "embed": 0}
    error: str | None = None
    stage = "extract"

    async def over_cap() -> bool:
        spent = await _spent_since(watermark)
        if spent >= cap_usd:
            log.warning("cap reached: $%.2f of $%.2f before the %s stage", spent, cap_usd, stage)
            return True
        return False

    for vehicle_id in ids:
        stats_e = await process.run_extract(None, vehicle_id)
        failed["extract"] += int(getattr(stats_e, "failed", 0) or 0)
    stage = "llm"
    for vehicle_id in ids:
        if await over_cap():
            error = "cap reached"
            break
        err, n_failed = await process._batch_llm_once(vehicle_id, cap_usd, watermark, per_doc)
        failed["llm"] += n_failed
        if err == "cap reached":
            error = err
            break
        if err:
            error = err
    stage = "embed"
    if error is None:
        for vehicle_id in ids:
            if await over_cap():
                error = "cap reached"
                break
            stats_m = await process.run_embed(None, vehicle_id)
            failed["embed"] += int(getattr(stats_m, "failed", 0) or 0)
    if error is None:
        await extract_stage.prune_archive()

    problems = [f"{s} failed={n}" for s, n in failed.items() if n]
    if error not in (None, "cap reached") or problems:
        error = "; ".join([e for e in [error, *problems] if e])
    tokens_in, tokens_out, cost = await _ledger_totals(watermark)
    await process.record_run(
        JOB_PROCESS,
        {
            "run": RUN_PROCESS,
            "vehicle_ids": ids,
            "stage_failures": failed,
            "cap_usd": cap_usd,
            "watermark": watermark.isoformat(),
        },
        tokens_in,
        tokens_out,
        cost,
        ok=error is None,
        error=error,
    )
    print(f"Process: spent ${cost:.4f} of ${cap_usd:.2f} since the watermark; {error or 'ok'}")
    if error == "cap reached":
        return CAP_EXIT_CODE
    return 0 if error is None else 1


async def run(argv: list[str] | None = None) -> int:
    """The CLI: parse ``argv`` and dry-run or apply. Returns the exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m src.scripts.prefill",
        description=(
            "Create the vehicles of a list, sync their bulletins and process them through "
            "Batch under a spend cap. The list is a JSON array of {year, make, model}; the "
            "default src/seed/top50.json holds 10 nameplates x 2020-2024. Dry-run by default."
        ),
    )
    parser.add_argument("--list", default=str(DEFAULT_LIST), help="list file (the top-50 seed)")
    parser.add_argument("--only", type=int, help="use only the first N entries")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan (the default)")
    mode.add_argument("--apply", action="store_true", help="create, sync and process")
    parser.add_argument("--cap-usd", type=float, default=DEFAULT_CAP_USD, help="stop at this spend")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    entries = load_list(args.list, args.only)
    if not args.apply:
        return await dry_run(entries, args.cap_usd)
    confirm = (
        None
        if args.yes
        else (lambda: input("Proceed? [y/N] ").strip().lower() in ("y", "yes"))
    )
    return await apply(entries, args.cap_usd, confirm)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def _main() -> int:
        try:
            return await run()
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
