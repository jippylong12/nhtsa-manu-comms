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


async def _seed_document(conn, tag: str) -> tuple[int, int]:
    comm = await conn.fetchval(
        "INSERT INTO communications (nhtsa_id, status) VALUES ($1, 'pending') RETURNING id", tag
    )
    doc = await conn.fetchval(
        "INSERT INTO comm_documents (communication_id, url, extraction_method, extracted_text, "
        "extracted_at, page_count) VALUES ($1, $2, 'pymupdf', 'words', now(), 2) RETURNING id",
        comm,
        f"https://example.test/{tag}.pdf",
    )
    return comm, doc


@pytest.mark.asyncio
async def test_a_document_inside_a_batch_that_is_still_out_is_not_read_by_anything_else():
    from src.jobs import llm

    tag = f"test-{uuid.uuid4().hex}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        comm, doc = await _seed_document(conn, tag)
    try:
        ids = lambda docs: {d["id"] for d in docs}  # noqa: E731
        assert doc in ids(await llm._pending_documents(None))

        await llm._record_batch(tag, [doc])
        # The sync path, an admin refresh and a re-extract all select through here.
        assert doc not in ids(await llm._pending_documents(None))
        assert tag in await process.open_batches()
        # Collection still finds it, to store the result it already paid for.
        assert doc in ids(await llm._batch_documents(tag))

        async with llm._storing(tag) as mine:
            assert mine is True
        async with llm._storing(tag) as mine:
            assert mine is False  # a second collector stores nothing
        assert tag not in await process.open_batches()
        # Stored or given up: if it is still unread it may be read again.
        assert doc in ids(await llm._pending_documents(None))
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM communications WHERE id = $1", comm)
            await conn.execute("DELETE FROM llm_batches WHERE batch_id = $1", tag)
