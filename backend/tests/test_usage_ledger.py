"""Cost ledger: pricing, the record_usage writer, aggregation, and the guarantee
that no billable call path skips metering.

DB-backed tests clean up the rows they create, keyed by a namespaced query_id so
a failed run cannot pollute real spend history.
"""

import pathlib
import re
import uuid

import pytest

from src.config import get_settings
from src.usage import pricing
from src.usage.ledger import KINDS, record_usage, summarise

NS = f"test-{uuid.uuid4().hex[:12]}"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _needs_db():
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not configured")


async def _cleanup(query_id: str) -> None:
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM usage_events WHERE query_id = $1", query_id)


# --------------------------------------------------------------------------- #
# Pricing is the single source of truth
# --------------------------------------------------------------------------- #
def test_cost_of_charges_input_and_output():
    rate = pricing.rate_for("gemini-3.1-flash-lite")
    got = pricing.cost_of("gemini-3.1-flash-lite", 1000, 500)
    assert got == pytest.approx(1000 * rate.input_per_token + 500 * rate.output_per_token)


def test_batch_is_half_price():
    full = pricing.cost_of("gemini-3.1-flash-lite", 1000, 500)
    batch = pricing.cost_of("gemini-3.1-flash-lite", 1000, 500, batch=True)
    assert batch == pytest.approx(full * pricing.BATCH_DISCOUNT)


def test_embedding_output_tokens_are_free():
    assert pricing.rate_for("gemini-embedding-001").output_per_token == 0.0


def test_unknown_model_falls_back_rather_than_reporting_zero():
    """An unpriced model must not silently look free in the ledger."""
    assert pricing.cost_of("some-future-model", 1_000_000, 0) > 0


def test_symptom_search_costs_use_the_same_rates():
    """The per-query figure shown to the user and the ledger must not drift."""
    from src.symptom_search import cost as sscost

    assert sscost.PRICE_EMBED_IN == pricing.rate_for("gemini-embedding-001").input_per_token
    assert sscost.llm_cost("gemini-3.1-flash-lite", 700, 200) == pytest.approx(
        pricing.cost_of("gemini-3.1-flash-lite", 700, 200)
    )


# --------------------------------------------------------------------------- #
# record_usage
# --------------------------------------------------------------------------- #
async def test_record_usage_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        await record_usage("not_a_kind", "gemini-3.1-flash-lite")


async def test_record_usage_writes_a_row_with_derived_cost():
    _needs_db()
    from src.db import get_pool

    qid = f"{NS}-write"
    try:
        returned = await record_usage(
            "answer_compose", "gemini-3.1-flash-lite", tokens_in=1000, tokens_out=500, query_id=qid
        )
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM usage_events WHERE query_id = $1", qid)
        assert row is not None
        assert row["tokens_in"] == 1000 and row["tokens_out"] == 500
        expected = pricing.cost_of("gemini-3.1-flash-lite", 1000, 500)
        assert float(row["cost_usd"]) == pytest.approx(expected, rel=1e-6)
        assert returned == pytest.approx(expected)
    finally:
        await _cleanup(qid)


async def test_record_usage_never_raises_when_the_write_fails(monkeypatch):
    """Metering must not be able to break the operation it meters."""
    from src.usage import ledger

    async def _boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(ledger, "get_pool", _boom)
    # Returns the cost it would have recorded, and does not propagate.
    assert await ledger.record_usage("search_embed", "gemini-embedding-001", tokens_in=10) > 0


async def test_summarise_totals_reconcile_with_the_breakdown():
    _needs_db()
    qid = f"{NS}-agg"
    try:
        await record_usage("search_embed", "gemini-embedding-001", tokens_in=10, query_id=qid)
        await record_usage(
            "answer_compose", "gemini-3.1-flash-lite", tokens_in=700, tokens_out=200, query_id=qid
        )
        summary = await summarise()
        assert summary["totals"]["events"] >= 2
        # Every breakdown must sum back to the same total, or the API lies.
        for axis in ("by_kind", "by_model", "by_day"):
            assert sum(r["cost_usd"] for r in summary[axis]) == pytest.approx(
                summary["totals"]["cost_usd"], rel=1e-6
            )
            assert sum(r["events"] for r in summary[axis]) == summary["totals"]["events"]
    finally:
        await _cleanup(qid)


async def _silverado_24() -> int:
    """Row id of the tracked 2024 Silverado with the biggest corpus (the API's vehicleId)."""
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row_id = await conn.fetchval(
            """
            SELECT v.id FROM vehicles v
            WHERE v.active AND v.year = 2024 AND v.model ILIKE 'SILVERADO%'
            ORDER BY (SELECT count(*) FROM communication_vehicles cv WHERE cv.vehicle_id = v.id)
                     DESC, v.id
            LIMIT 1
            """
        )
    if row_id is None:
        pytest.skip("no tracked 2024 Silverado in this database")
    return row_id


# --------------------------------------------------------------------------- #
# End to end: a real search must land in the ledger (JIP-409 acceptance)
# --------------------------------------------------------------------------- #
async def test_a_symptom_search_records_its_spend():
    _needs_db()
    if not get_settings().gemini_api_key:
        pytest.skip("GEMINI_API_KEY not configured")

    from src.db import get_pool
    from src.symptom_search import service

    before = await summarise()
    result = await service.search(await _silverado_24(), "the tailgate will not open")

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT kind, tokens_in, tokens_out, cost_usd
            FROM usage_events
            WHERE occurred_at > now() - interval '2 minutes'
              AND kind IN ('search_embed', 'answer_compose')
            ORDER BY id DESC LIMIT 2
            """
        )
    kinds = {r["kind"] for r in rows}
    assert "search_embed" in kinds, "query embedding was not metered"
    if not result["refused"]:
        assert "answer_compose" in kinds, "answer composition was not metered"

    # The ledger's view of this query must equal what the API reported.
    ledger_cost = sum(float(r["cost_usd"]) for r in rows)
    assert ledger_cost == pytest.approx(result["cost"]["total_usd"], rel=1e-3)

    after = await summarise()
    assert after["totals"]["cost_usd"] > before["totals"]["cost_usd"]


# --------------------------------------------------------------------------- #
# No unmetered billable path
# --------------------------------------------------------------------------- #
# Modules that call the model SDK but are deliberately metered by their caller.
METERED_BY_CALLER = {
    # compose._generate is metered by symptom_search/service.py, which owns the
    # query_id that ties the composition to its embedding.
    "symptom_search/compose.py": "metered by symptom_search/service.py",
}

SDK_CALL = re.compile(r"generate_content|embed_content|batches\.create|messages\.create|messages\.stream|messages\.batches\.create")


def test_every_module_that_calls_the_model_sdk_records_usage():
    """Guards the acceptance criterion that no billable path skips the ledger.

    A new call site either records usage itself or must be added to
    METERED_BY_CALLER with a reason, which makes skipping it a deliberate act.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        if not SDK_CALL.search(text):
            continue
        rel = str(path.relative_to(SRC))
        if "record_usage" in text or rel in METERED_BY_CALLER:
            continue
        offenders.append(rel)
    assert not offenders, f"billable call sites with no ledger write: {offenders}"


def test_ledger_kinds_match_the_database_constraint():
    """KINDS must stay in sync with the CHECK in 0003_usage_events.sql."""
    sql = (SRC.parents[0] / "migrations" / "0003_usage_events.sql").read_text()
    constraint = re.search(r"kind IN \(([^)]+)\)", sql, re.S).group(1)
    in_sql = set(re.findall(r"'([a-z_]+)'", constraint))
    assert in_sql == set(KINDS)


async def test_a_billed_composition_that_fails_to_parse_is_still_metered(monkeypatch):
    """Composition can be billed and *then* fail (an unparseable response).

    That spend must still reach the ledger, or a billable call escapes metering.
    """
    _needs_db()
    from src.db import get_pool
    from src.symptom_search import compose as compose_mod
    from src.symptom_search import service

    # A response the model was paid for, but which will not parse.
    def _unparseable(model: str, prompt: str):
        return "not json at all", 700, 200

    monkeypatch.setattr(compose_mod, "_generate", _unparseable)

    pool = await get_pool()
    async with pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE kind = 'answer_compose'"
        )

    result = await service.search(await _silverado_24(), "the tailgate will not open")
    # The request degrades rather than failing outright...
    assert result["answer_generated"] is False

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT tokens_in, tokens_out, cost_usd, meta
            FROM usage_events WHERE kind = 'answer_compose'
            ORDER BY id DESC LIMIT 1
            """
        )
        after = await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE kind = 'answer_compose'"
        )

    # ...but the tokens it was billed for are on the ledger.
    assert after == before + 1, "a billed composition escaped the ledger"
    assert row["tokens_in"] == 700 and row["tokens_out"] == 200
    assert float(row["cost_usd"]) == pytest.approx(
        pricing.cost_of("gemini-3.5-flash-lite", 700, 200), rel=1e-6
    )
