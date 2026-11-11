"""The `llm_batches` ledger: what is out stays out until its results are stored."""

import uuid

import pytest

from src.db import get_pool
from src.jobs import process

pytestmark = pytest.mark.real_batch_ledger


@pytest.mark.asyncio
async def test_a_tracked_batch_is_open_until_it_is_marked_collected():
    one, two = f"test-{uuid.uuid4().hex}", f"test-{uuid.uuid4().hex}"
    try:
        await process.track_batches([one, two])
        await process.track_batches([one])  # tracking twice is harmless
        assert {one, two} <= set(await process.open_batches())

        await process.mark_batches_collected([one])
        still_out = await process.open_batches()
        assert one not in still_out and two in still_out
    finally:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM llm_batches WHERE batch_id = ANY($1::text[])", [one, two]
            )
