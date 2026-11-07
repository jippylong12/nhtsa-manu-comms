"""Run the pipeline for one vehicle, from inside the app (JIP-418).

The pipeline could already take a vehicle from nothing to fully analysed; it
just had no door into it except two CLI commands. This is that door.

Shape: **preview then run.** A click here can spend real money and take a
quarter of an hour, so the free preview produces an estimate and the run only
starts once the user has confirmed it.

Progress lives in the `refresh_runs` table rather than in a held-open SSE
connection. A run outlasts any reasonable HTTP timeout and the user is expected
to navigate away and come back, so the state has to survive both.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from src.communications import flatfile
from src.communications.nhtsa_client import NHTSAClient
from src.db import get_pool
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage
from src.jobs import process as processor
from src.jobs import repository as repo
from src.jobs import sync as sync_job
from src.refresh import estimate
from src.vehicles.service import VehicleService

log = logging.getLogger("refresh")

# A run whose heartbeat has gone quiet for this long is treated as abandoned.
# Generous because the Gemini batch is the long silent stretch, and the stages
# heartbeat around it rather than through it.
STALE_AFTER = timedelta(minutes=30)

# Strong references to in-flight background tasks. asyncio only holds a weak
# reference to a running task, so without this the garbage collector is free to
# cancel a refresh mid-run.
_running: set[asyncio.Task] = set()


class RefreshConflict(Exception):
    """Another refresh is already active."""

    def __init__(self, active: dict[str, Any]):
        self.active = active
        super().__init__("a refresh is already running")


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


async def preview(vehicle_id: int) -> Optional[dict[str, Any]]:
    """What a refresh would do, and what it would cost. Spends nothing.

    Two kinds of work can be outstanding, and the estimate has to cover both or
    it will under-quote:

    * bulletins the flat-file index lists that the corpus has never seen, and
    * documents already in the corpus for this vehicle that were never
      analysed, left behind by an interrupted or partial earlier run.

    The second is exactly the state the user currently sees as "pending", so a
    refresh that ignored it would leave the vehicle looking half-finished after
    reporting success.

    The bulletin list now comes from the local `comm_products` index rather than a
    live `get_vehicle_details` call, so the preview no longer touches NHTSA and can
    no longer 504.
    """
    vehicle = await VehicleService.get_vehicle(vehicle_id)
    if vehicle is None:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        listed = [
            r["nhtsa_id"]
            for r in await conn.fetch(
                "SELECT nhtsa_id FROM comm_products "
                "WHERE model_year = $1 AND make = $2 AND model = $3",
                int(vehicle["year"]),
                str(vehicle["make"]).upper(),
                flatfile.normalize_model(vehicle["model"]),
            )
        ]
        known = await repo.existing_nhtsa_ids(conn, listed)
        unfinished = await conn.fetchval(
            f"""
            SELECT count(*)
            FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            WHERE c.status = 'pending'
              AND d.llm_summary IS NULL
              AND {repo.vehicle_scope('$1')}
            """,
            vehicle_id,
        )

    new_bulletins = len(set(listed) - known)
    per_bulletin = await estimate.documents_per_bulletin()
    documents = round(new_bulletins * per_bulletin) + unfinished
    per_document = await estimate.cost_per_document()

    return {
        "vehicle_id": vehicle_id,
        "listed_by_nhtsa": len(listed),
        "new_bulletins": new_bulletins,
        "unfinished_documents": unfinished,
        "documents_to_process": documents,
        "up_to_date": documents == 0,
        **estimate.summarise(new_bulletins, documents, per_document),
    }


# --------------------------------------------------------------------------
# Run state
# --------------------------------------------------------------------------

_RUN_COLUMNS = """
    r.id, r.vehicle_id, r.status, r.stage, r.stage_note,
    r.done, r.total, r.expected_documents, r.estimated_cost_usd,
    r.actual_cost_usd, r.counts, r.error,
    r.started_at, r.heartbeat_at, r.finished_at
"""


def _row(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    # numeric comes back as Decimal, which does not survive JSON serialisation.
    d["estimated_cost_usd"] = float(d["estimated_cost_usd"])
    if d["actual_cost_usd"] is not None:
        d["actual_cost_usd"] = float(d["actual_cost_usd"])
    # asyncpg hands back jsonb as the raw text, not a dict. Without this the
    # status endpoint 500s on its own response model the moment a run exists.
    if isinstance(d.get("counts"), str):
        d["counts"] = json.loads(d["counts"])
    d["counts"] = d["counts"] or {}
    return d


async def latest_run(vehicle_id: int) -> Optional[dict[str, Any]]:
    """The most recent run for a vehicle, finished or not."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"""
                SELECT {_RUN_COLUMNS} FROM refresh_runs r
                WHERE r.vehicle_id = $1
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                vehicle_id,
            )
        )


async def active_run() -> Optional[dict[str, Any]]:
    """The run currently holding the lock, if any."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"""
                SELECT {_RUN_COLUMNS} FROM refresh_runs r
                WHERE r.active_lock IS NOT NULL
                """
            )
        )


async def _reap_abandoned() -> None:
    """Fail any active run whose worker has stopped reporting.

    Without this a process killed mid-run (a dev-server reload, a machine
    sleeping) leaves one row holding the global lock and no refresh can ever
    start again.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        reaped = await conn.fetch(
            """
            UPDATE refresh_runs
            SET status = 'failed', active_lock = NULL, finished_at = now(),
                error = 'the run stopped reporting and was assumed abandoned'
            WHERE active_lock IS NOT NULL AND heartbeat_at < now() - $1::interval
            RETURNING id
            """,
            STALE_AFTER,
        )
    for r in reaped:
        log.warning("reaped abandoned refresh run %s", r["id"])


async def _progress(
    run_id: int,
    *,
    stage: Optional[str] = None,
    note: Optional[str] = None,
    done: Optional[int] = None,
    total: Optional[int] = None,
) -> None:
    """Write a progress point and beat the heartbeat.

    Every field is optional and NULL leaves the column alone, so a caller that
    only has a new note does not have to restate the counters.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE refresh_runs
            SET stage        = coalesce($2, stage),
                stage_note   = coalesce($3, stage_note),
                done         = coalesce($4, done),
                total        = coalesce($5, total),
                status       = CASE WHEN status = 'queued' THEN 'running' ELSE status END,
                heartbeat_at = now()
            WHERE id = $1
            """,
            run_id,
            stage,
            note,
            done,
            total,
        )


async def start(vehicle_id: int) -> dict[str, Any]:
    """Begin a run. Raises RefreshConflict if one is already active."""
    vehicle = await VehicleService.get_vehicle(vehicle_id)
    if vehicle is None:
        raise LookupError(f"vehicle {vehicle_id} is not tracked")

    await _reap_abandoned()

    plan = await preview(vehicle_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            # Two statements, not an INSERT wrapped in a CTE that a SELECT then
            # reads: the rest of a statement sees the snapshot from before its
            # data-modifying CTE ran, so the SELECT would find no row and this
            # would hand back None while the insert had in fact happened,
            # stranding the run holding the lock with no worker behind it.
            run_id = await conn.fetchval(
                """
                INSERT INTO refresh_runs (
                    vehicle_id, status, stage, stage_note,
                    total, expected_documents, estimated_cost_usd, active_lock
                )
                SELECT v.id, 'queued', 'sync', 'Starting', $2, $2, $3, true
                FROM vehicles v WHERE v.id = $1
                RETURNING id
                """,
                vehicle_id,
                plan["documents_to_process"],
                plan["estimated_cost_high_usd"],
            )
        except asyncpg.UniqueViolationError as e:
            # The partial unique index on active_lock is what rejects a second
            # concurrent run. Report who holds it rather than a bare 500.
            active = await active_run()
            raise RefreshConflict(active or {}) from e

        row = await conn.fetchrow(
            f"""
            SELECT {_RUN_COLUMNS} FROM refresh_runs r
            WHERE r.id = $1
            """,
            run_id,
        )

    run = _row(row)
    task = asyncio.create_task(_execute(run["id"], vehicle_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return run


# --------------------------------------------------------------------------
# The run itself
# --------------------------------------------------------------------------


async def _finish(
    run_id: int, status: str, *, error: Optional[str] = None, counts: Optional[dict] = None
) -> None:
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Attribute spend by time: only one run is ever active, so everything
        # metered since this one started belongs to it.
        actual = await conn.fetchval(
            """
            SELECT coalesce(sum(cost_usd), 0) FROM usage_events
            WHERE occurred_at >= (SELECT started_at FROM refresh_runs WHERE id = $1)
              AND kind IN ('extract', 'llm_extract', 'embed')
            """,
            run_id,
        )
        await conn.execute(
            """
            UPDATE refresh_runs
            SET status = $2, error = $3, counts = $4::jsonb, actual_cost_usd = $5,
                active_lock = NULL, finished_at = now(), heartbeat_at = now(),
                stage = 'finalise'
            WHERE id = $1
            """,
            run_id,
            status,
            error,
            json.dumps(counts or {}),
            actual,
        )


async def _vehicle_state(vehicle_id: int) -> dict[str, Any]:
    """How the vehicle's corpus stands, for the run's closing report."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
              count(*) FILTER (WHERE c.status = 'processed') AS analysed,
              count(*) FILTER (WHERE c.status = 'pending')   AS still_working,
              count(*) FILTER (WHERE c.status = 'failed'
                               AND c.attempts >= $2)         AS gave_up,
              count(*) FILTER (WHERE c.status = 'failed'
                               AND c.attempts < $2)          AS unavailable,
              count(*)                                       AS total
            FROM communications c
            WHERE {repo.vehicle_scope('$1')}
            """,
            vehicle_id,
            llm_stage.MAX_LLM_ATTEMPTS,
        )
    return {k: int(v) for k, v in dict(row).items()}


async def _freshen_index() -> None:
    """Re-ingest any flat-file windows whose NHTSA ETag changed. Never raises.

    The index is the cache both discovery and sync read from, and this keeps it
    current at the head of the run. It is ETag-guarded, so an unchanged corpus
    costs only eight HEAD requests; and a failure leaves the previous index in
    place -- bounded staleness rather than breakage, which is the whole point of
    the flat-file design -- so it must never fail the run.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            report = await flatfile.refresh_index(conn)
        changed = {w: r.get("rows") for w, r in report.items() if r.get("status") == "ingested"}
        if changed:
            log.info("flat-file index refreshed: %s", changed)
    except Exception as e:  # noqa: BLE001 - a stale index still serves; never fail the run
        log.warning("flat-file index refresh skipped: %s: %s", type(e).__name__, e)


async def _execute(run_id: int, vehicle_id: int) -> None:
    """The background worker. Never raises; every exit updates the run row."""
    try:
        # ---- keep the flat-file index current (best effort) ------------
        await _progress(run_id, stage="sync", note="Checking NHTSA flat files for updates", done=0)
        await _freshen_index()

        # ---- sync ------------------------------------------------------
        await _progress(run_id, stage="sync", note="Checking NHTSA for new bulletins", done=0)
        pool = await get_pool()
        async with pool.acquire() as conn:
            vehicles = [
                v
                for v in await repo.tracked_vehicles(conn)
                if v["id"] == vehicle_id
            ]
            if not vehicles:
                await _finish(run_id, "failed", error="the vehicle is no longer tracked")
                return
            client = NHTSAClient()
            stats = await sync_job.sync_vehicle(conn, client, vehicles[0])

        await VehicleService.touch_fetched_at(vehicle_id)
        await _progress(
            run_id,
            stage="read",
            note=f"Found {stats.new_comms} new bulletin(s). Reading documents",
        )

        # ---- extract ---------------------------------------------------
        extracted = await processor.run_extract(None, vehicle_id=vehicle_id)
        await _progress(
            run_id,
            stage="analyse",
            note="Analysing documents. This is the long part and is queued with the model provider",
            done=0,
        )

        # ---- llm -------------------------------------------------------
        await processor.run_llm(None, sync=False, vehicle_id=vehicle_id)
        await llm_stage.promote_completed_communications()
        await _progress(run_id, stage="index", note="Indexing for search")

        # ---- embed -----------------------------------------------------
        await processor.run_embed(None, vehicle_id=vehicle_id)

        # ---- housekeeping ----------------------------------------------
        await _progress(run_id, stage="finalise", note="Tidying up")
        try:
            removed, freed = await extract_stage.prune_archive()
            log.info("pruned %d pdf(s), freed %.1f MB", removed, freed / 1_048_576)
        except Exception as e:  # noqa: BLE001 - reclaiming disk never fails a run
            log.warning("archive prune skipped: %s: %s", type(e).__name__, e)

        state = await _vehicle_state(vehicle_id)
        counts = {
            "new_bulletins": stats.new_comms,
            "new_documents": stats.new_docs,
            "already_known": stats.skipped_known,
            "fetch_failures": stats.fetch_failures,
            "extracted": extracted.extracted,
            **state,
        }
        # Partial failure is reported, not hidden. A document that has run out
        # of retries is a different thing from one still queued, and the run
        # says which.
        if state["still_working"] or state["gave_up"]:
            note = []
            if state["gave_up"]:
                note.append(f"{state['gave_up']} bulletin(s) could not be analysed")
            if state["still_working"]:
                note.append(f"{state['still_working']} still waiting to be analysed")
            await _finish(run_id, "succeeded", error="; ".join(note), counts=counts)
        else:
            await _finish(run_id, "succeeded", counts=counts)

    except asyncio.CancelledError:
        await _finish(run_id, "failed", error="the run was cancelled")
        raise
    except Exception as e:  # noqa: BLE001 - the row is the only failure channel
        log.exception("refresh run %s failed", run_id)
        await _finish(run_id, "failed", error=f"{type(e).__name__}: {e}")


def stale_cutoff(now: Optional[datetime] = None) -> datetime:
    """Exposed for tests."""
    return (now or datetime.now(timezone.utc)) - STALE_AFTER
