"""Keeps checking back on Batch API jobs until their results are stored.

The nightly run submits its documents as a batch and waits a while for it. Anthropic may
take up to 24 hours, so a batch can outlive that wait. It is paid for when it ends whether
or not anyone collects it, so this loop, started with the API, looks at every batch still
out (`llm_batches`) on a timer and stores the results of the ones that ended. With nothing
out, a check is one small query and no model call.
"""

import asyncio
import logging
from typing import Optional

from src.jobs import process as processor

log = logging.getLogger("batch_watch")

# How often to look. A check never waits on Anthropic and never submits anything.
CHECK_SECONDS = 15 * 60

_task: Optional[asyncio.Task] = None


def ensure_running() -> None:
    """Start the watch unless it is already running."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_watch())


async def check_once() -> int:
    """One look at the batches still out. Never raises: the watch must outlive any error."""
    try:
        stored = await processor.collect_open_batches()
    except Exception:  # noqa: BLE001
        log.exception("batch check failed; it will be tried again")
        return 0
    if stored:
        log.info("stored %d document(s) from a batch that ended", stored)
    return stored


async def _watch() -> None:
    while True:
        await check_once()
        await asyncio.sleep(CHECK_SECONDS)
