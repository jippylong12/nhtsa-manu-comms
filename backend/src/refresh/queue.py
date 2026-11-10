"""The queue of cars waiting to be read, and the worker that reads them.

Adding a car puts it in ``ingest_queue`` and wakes the worker. The worker is one
asyncio task inside the API: it reads the oldest queued car, then the next, and
ends when the queue is empty, so nothing runs while there is nothing to do. The
next add (or the next start of the API) starts it again.

It shares the daily spend cap with the nightly run (``NIGHTLY_SPEND_CAP_USD``,
counted over the last 24 hours of model spend). At the cap the worker stops and
leaves the queue as it is; the cars are read once spend is back under the cap,
by a later add waking the worker or by the nightly run, whichever comes first.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.config import get_settings
from src.db import get_pool
from src.jobs import process as processor
from src.refresh import service as refresh_service

log = logging.getLogger("ingest-queue")

# A read that keeps failing stops being retried here; the nightly run still reads the car.
MAX_ATTEMPTS = 3
# How long to wait when an admin's manual refresh holds the single run slot.
BUSY_WAIT_SECONDS = 20.0

_worker: Optional[asyncio.Task] = None


async def enqueue(vehicle_id: int) -> None:
    """Queue a car. Queuing one that is already waiting gives it a fresh set of attempts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ingest_queue (vehicle_id) VALUES ($1)
            ON CONFLICT (vehicle_id) DO UPDATE SET attempts = 0, last_error = NULL
            """,
            vehicle_id,
        )


async def request(vehicle_id: int) -> None:
    """What adding a car calls: queue it and make sure the worker is running."""
    await enqueue(vehicle_id)
    ensure_worker()


def ensure_worker() -> None:
    """Start the worker unless it is already running."""
    global _worker
    if _worker is not None and not _worker.done():
        return
    _worker = asyncio.create_task(_run_worker())


async def _run_worker() -> None:
    try:
        await drain()
    except Exception:  # noqa: BLE001
        # The worker must never take the API down; the queue rows remain for the next wake.
        log.exception("ingest worker stopped on an error")


async def cap_reached(now: Optional[datetime] = None) -> bool:
    """True when the last 24 hours of model spend has reached the daily cap."""
    cap = float(get_settings().nightly_spend_cap_usd)
    if cap <= 0:
        return False
    now = now or datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        spent = await processor._spent_since(conn, now - timedelta(hours=24))
    return spent >= cap


async def _next_vehicle() -> Optional[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT vehicle_id FROM ingest_queue WHERE attempts < $1 "
            "ORDER BY requested_at, vehicle_id LIMIT 1",
            MAX_ATTEMPTS,
        )


async def _done(vehicle_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM ingest_queue WHERE vehicle_id = $1", vehicle_id)


async def _failed(vehicle_id: int, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ingest_queue SET attempts = attempts + 1, last_error = $2 "
            "WHERE vehicle_id = $1",
            vehicle_id,
            error[:500],
        )


async def drain() -> int:
    """Read queued cars, oldest first, until the queue is empty or the cap is reached.

    Returns how many cars were read successfully.
    """
    read = 0
    while True:
        vehicle_id = await _next_vehicle()
        if vehicle_id is None:
            return read
        if await cap_reached():
            log.warning(
                "daily spend cap of $%.2f reached: queued cars wait until spend drops",
                get_settings().nightly_spend_cap_usd,
            )
            return read
        try:
            run = await refresh_service.run_now(vehicle_id)
        except refresh_service.RefreshConflict:
            # An admin's manual refresh holds the one run slot. Wait, then try the same car.
            await asyncio.sleep(BUSY_WAIT_SECONDS)
            continue
        except LookupError:
            # The vehicle is gone or no longer tracked: nothing to read.
            await _done(vehicle_id)
            continue
        except Exception as e:  # noqa: BLE001
            log.exception("reading vehicle %s failed", vehicle_id)
            await _failed(vehicle_id, f"{type(e).__name__}: {e}")
            continue
        if run and run.get("status") == "succeeded":
            await _done(vehicle_id)
            read += 1
        else:
            await _failed(vehicle_id, (run or {}).get("error") or "the read did not finish")
