"""Retry behaviour for LLM extraction failures.

A malformed model response used to be terminal: _mark_failed wrote
status='failed', and document selection keys on status='pending', so the
document was never offered to the model again. One 2018 F-150 bulletin was lost
that way despite being an ordinary two-page PDF that any later run would have
handled.

Runs against the real database inside a namespaced fixture removed afterwards.
"""

import uuid

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs.llm import MAX_LLM_ATTEMPTS, RESPONSE_SCHEMA, _mark_failed, _params, anthropic_schema

NS = f"TESTRETRY-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def comm():
    """One pending communication, and a reader for its current state."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    conn = await asyncpg.connect(settings.database_url)
    try:
        comm_id = await conn.fetchval(
            """
            INSERT INTO communications (nhtsa_id, communication_type, communication_date, status)
            VALUES ($1, 'TSB', '2099-01-01', 'pending') RETURNING id
            """,
            NS,
        )

        async def state():
            r = await conn.fetchrow(
                "SELECT status, attempts, status_reason FROM communications WHERE id=$1", comm_id
            )
            return dict(r)

        yield comm_id, state
    finally:
        await conn.execute("DELETE FROM communications WHERE nhtsa_id = $1", NS)
        await conn.close()


@pytest.mark.asyncio
async def test_first_failure_stays_pending_so_the_next_run_retries(comm):
    comm_id, state = comm

    await _mark_failed(comm_id, "LLM schema violation: invalid JSON")

    s = await state()
    assert s["status"] == "pending", "a transient LLM error must not be terminal"
    assert s["attempts"] == 1
    assert "invalid JSON" in s["status_reason"]


@pytest.mark.asyncio
async def test_failure_becomes_terminal_once_the_budget_is_spent(comm):
    comm_id, state = comm

    for _ in range(MAX_LLM_ATTEMPTS):
        await _mark_failed(comm_id, "still broken")

    s = await state()
    assert s["status"] == "failed", "a document that never parses must stop costing money"
    assert s["attempts"] == MAX_LLM_ATTEMPTS


@pytest.mark.asyncio
async def test_retryable_false_fails_immediately(comm):
    """For something re-asking cannot fix."""
    comm_id, state = comm

    await _mark_failed(comm_id, "unsupported document", retryable=False)

    assert (await state())["status"] == "failed"


@pytest.mark.asyncio
async def test_a_processed_communication_is_never_dragged_back(comm):
    """A late failure on one document must not undo an already-finished comm."""
    comm_id, state = comm
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("UPDATE communications SET status='processed' WHERE id=$1", comm_id)
        await _mark_failed(comm_id, "late failure")
    finally:
        await conn.close()

    assert (await state())["status"] == "processed"


# --------------------------------------------------------------------------- #
# The request shape
# --------------------------------------------------------------------------- #
def test_the_request_asks_for_low_effort_and_a_closed_json_schema():
    params = _params("hello")

    assert params["max_tokens"] == 8192
    assert params["output_config"]["effort"] == "low"
    fmt = params["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert params["messages"] == [{"role": "user", "content": "hello"}]


def test_the_request_never_carries_temperature_or_thinking():
    params = _params("hello")

    assert "temperature" not in params and "thinking" not in params


def test_the_shared_response_schema_is_left_untouched_by_the_anthropic_copy():
    anthropic_schema()

    assert "additionalProperties" not in RESPONSE_SCHEMA
