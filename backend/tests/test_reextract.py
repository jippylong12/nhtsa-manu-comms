"""process --reextract: reset, re-read, re-embed and re-classify under a spend cap.

Runs against the test database with namespaced rows removed afterwards. The four
stages (extract, llm, embed, prune) are monkeypatched to record calls and to write
fake ledger rows, so nothing here calls a model or touches the PDF archive.
"""

import json
import random
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs import embed as embed_stage
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage
from src.jobs import process
from src.jobs.extract import ExtractStats
from src.jobs.sync import SUMMARY_ONLY_REASON

NS = f"TESTREEX-{uuid.uuid4().hex[:8]}"
VECTOR = "[" + ",".join(["0.1"] * 1536) + "]"


class World:
    """Rows for one test, plus a reader and a recorder of stage calls."""

    def __init__(self, conn):
        self.conn = conn
        self.calls: list[tuple] = []
        self._n = 0
        self.baseline_run = 0

    async def vehicle(self) -> int:
        self._n += 1
        return await self.conn.fetchval(
            "INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model) "
            "VALUES ($1, 2099, $2, $3) RETURNING id",
            random.randint(10**8, 10**9),
            NS,
            f"M{self._n}",
        )

    async def comm(
        self,
        vehicle_id: int,
        docs: int = 1,
        *,
        status: str = "processed",
        summarised: bool = True,
        reason: str | None = None,
        ctype: str = "TSB",
        severity: str = "small",
        severity_source: str = "classifier",
    ) -> int:
        self._n += 1
        comm_id = await self.conn.fetchval(
            """
            INSERT INTO communications (nhtsa_id, communication_type, communication_date, status,
                status_reason, attempts, processed_at, severity, severity_source)
            VALUES ($1, $2, '2099-01-01', $3, $4, 2, now(), $5, $6) RETURNING id
            """,
            f"{NS}-{self._n}",
            ctype,
            status,
            reason,
            severity,
            severity_source,
        )
        await self.conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
            comm_id,
            vehicle_id,
        )
        for i in range(docs):
            await self.conn.execute(
                """
                INSERT INTO comm_documents (communication_id, url, extracted_text, extracted_at,
                    llm_summary, doc_kind, symptoms, systems, components, remedy, applicability,
                    applicability_years, applicability_models, applicability_conditions, audience,
                    llm_model, llm_tokens_in, llm_tokens_out, llm_cost_usd, llm_at,
                    embedding, embedding_model, embedded_at)
                VALUES ($1, $2, 'text', now(),
                    $3, $4, '{rattle}', '{brakes}', '{pad}', 'fix', 'all',
                    '{2020}', '{F-150}', 'when hot', 'owner',
                    'haiku', 10, 5, 0.001, now(),
                    $5::vector, 'emb', now())
                """,
                comm_id,
                f"https://{NS}/{comm_id}/{i}.pdf",
                "summary" if summarised else None,
                "recall" if summarised else None,
                VECTOR if summarised else None,
            )
        return comm_id

    async def doc_rows(self, comm_id: int) -> list[dict]:
        rows = await self.conn.fetch(
            "SELECT * FROM comm_documents WHERE communication_id=$1 ORDER BY id", comm_id
        )
        return [dict(r) for r in rows]

    async def comm_row(self, comm_id: int) -> dict:
        return dict(await self.conn.fetchrow("SELECT * FROM communications WHERE id=$1", comm_id))

    async def ledger(self, cost: float, kind: str = "llm_extract") -> None:
        await self.conn.execute(
            "INSERT INTO usage_events (kind, cost_usd, meta) VALUES ($1, $2, $3::jsonb)",
            kind,
            cost,
            json.dumps({"ns": NS}),
        )

    async def last_run(self, job: str) -> dict | None:
        row = await self.conn.fetchrow(
            "SELECT * FROM pipeline_runs WHERE job=$1 AND id > $2 ORDER BY id DESC LIMIT 1",
            job,
            self.baseline_run,
        )
        return dict(row) if row else None

    async def pending_docs(self, vehicle_id: int) -> int:
        return await self.conn.fetchval(
            """
            SELECT count(*) FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE cv.vehicle_id = $1 AND d.llm_summary IS NULL AND c.status = 'pending'
            """,
            vehicle_id,
        )


@pytest_asyncio.fixture
async def world(monkeypatch):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    w = World(conn)
    w.baseline_run = await conn.fetchval("SELECT coalesce(max(id), 0) FROM pipeline_runs")

    # The stages: record the call; the llm fake marks up to `limit` pending documents of the
    # scope as summarised and can write ledger rows through `w.llm_cost`.
    w.llm_cost = 0.0
    w.llm_idle = False  # True: the llm fake does nothing, so the reset state stays visible

    async def fake_extract(limit=None, vehicle_id=None):
        w.calls.append(("extract", limit, vehicle_id))
        return ExtractStats()

    async def fake_mark_summary_only(vehicle_id=None):
        return 0

    async def fake_run_sync(limit=None, concurrency=None, vehicle_id=None):
        w.calls.append(("llm", limit, vehicle_id))
        rows = (
            []
            if w.llm_idle
            else await conn.fetch(
                """
            SELECT d.id, d.communication_id FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE cv.vehicle_id = $1 AND d.llm_summary IS NULL AND c.status = 'pending'
            ORDER BY d.id LIMIT $2
            """,
                vehicle_id,
                limit,
            )
        )
        for r in rows:
            await conn.execute("UPDATE comm_documents SET llm_summary='new' WHERE id=$1", r["id"])
            await conn.execute(
                "UPDATE communications SET status='processed' WHERE id=$1", r["communication_id"]
            )
        if w.llm_cost:
            await w.ledger(w.llm_cost)
        return SimpleNamespace(
            processed=len(rows),
            failed=0,
            retried=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            errors=[],
        )

    async def fake_embed(limit=None, vehicle_id=None):
        w.calls.append(("embed", limit, vehicle_id))
        return SimpleNamespace(embedded=0, failed=0, tokens_in=0, cost_usd=0.0, errors=[])

    async def fake_prune(dry_run=False):
        w.calls.append(("prune",))
        return 0, 0

    monkeypatch.setattr(extract_stage, "extract_pending", fake_extract)
    monkeypatch.setattr(extract_stage, "mark_summary_only_communications", fake_mark_summary_only)
    monkeypatch.setattr(llm_stage, "run_sync", fake_run_sync)
    monkeypatch.setattr(embed_stage, "embed_pending", fake_embed)
    monkeypatch.setattr(extract_stage, "prune_archive", fake_prune)
    try:
        yield w
    finally:
        await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS}-%")
        await conn.execute("DELETE FROM vehicles WHERE make = $1", NS)
        await conn.execute("DELETE FROM usage_events WHERE meta->>'ns' = $1", NS)
        await conn.execute(
            "DELETE FROM pipeline_runs WHERE job IN ('reextract', 'reextract-reset') AND id > $1",
            w.baseline_run,
        )
        await conn.close()


@pytest.mark.asyncio
async def test_reset_clears_llm_and_embedding_fields_and_leaves_a_summary_only_row_alone(world):
    vehicle = await world.vehicle()
    done = await world.comm(vehicle, docs=2, ctype="MC", severity="serious")
    summary_only = await world.comm(
        vehicle, docs=0, reason=SUMMARY_ONLY_REASON, severity="medium", severity_source="type"
    )
    world.llm_idle = True

    await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    for doc in await world.doc_rows(done):
        for col in (
            "llm_summary",
            "doc_kind",
            "remedy",
            "applicability",
            "applicability_conditions",
            "audience",
            "llm_model",
            "llm_tokens_in",
            "llm_tokens_out",
            "llm_cost_usd",
            "llm_at",
            "embedding",
            "embedding_model",
            "embedded_at",
        ):
            assert doc[col] is None, col
        for col in (
            "symptoms",
            "systems",
            "components",
            "applicability_years",
            "applicability_models",
        ):
            assert doc[col] == [], col
        assert doc["extracted_text"] == "text", "the extracted text is kept"

    comm = await world.comm_row(done)
    assert (comm["status"], comm["status_reason"], comm["processed_at"], comm["attempts"]) == (
        "pending",
        None,
        None,
        0,
    )
    assert (comm["severity"], comm["severity_source"]) == (
        "small",
        "type",
    ), "an MC communication goes back to its type-derived severity"

    untouched = await world.comm_row(summary_only)
    assert untouched["status"] == "processed"
    assert untouched["status_reason"] == SUMMARY_ONLY_REASON
    assert untouched["attempts"] == 2

    reset_run = await world.last_run("reextract-reset")
    assert json.loads(reset_run["counts"])["documents"] == 2


@pytest.mark.asyncio
async def test_vehicle_flag_resets_only_that_vehicles_documents(world):
    mine, other = await world.vehicle(), await world.vehicle()
    mine_comm = await world.comm(mine)
    other_comm = await world.comm(other)

    await process.reextract(mine, cap_usd=5.0, sync=True, limit=None)

    assert (await world.comm_row(mine_comm))["status"] == "processed"  # re-read by the fake llm
    assert (await world.doc_rows(mine_comm))[0]["llm_summary"] == "new"
    untouched = await world.doc_rows(other_comm)
    assert untouched[0]["llm_summary"] == "summary"
    assert untouched[0]["embedding"] is not None
    assert (await world.comm_row(other_comm))["status"] == "processed"
    assert all(c[2] == mine for c in world.calls if c[0] in ("extract", "llm", "embed"))


@pytest.mark.asyncio
async def test_a_run_under_the_cap_extracts_then_runs_two_llm_slices_then_embeds(world):
    vehicle = await world.vehicle()
    for _ in range(75):
        await world.comm(vehicle)

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    assert code == 0
    assert [c[0] for c in world.calls] == ["extract", "llm", "llm", "embed", "prune"]
    assert [c[1] for c in world.calls if c[0] == "llm"] == [50, 50]
    assert await world.pending_docs(vehicle) == 0
    final = await world.last_run("reextract")
    assert final["ok"] is True
    assert json.loads(final["counts"])["documents"] == 75


@pytest.mark.asyncio
async def test_the_run_stops_once_the_ledger_reaches_the_cap_and_leaves_the_rest_pending(world):
    vehicle = await world.vehicle()
    for _ in range(60):
        await world.comm(vehicle)
    world.llm_cost = 6.0  # the first slice spends $6 against a $5 cap

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    assert code == 3
    assert [c[0] for c in world.calls] == ["extract", "llm"]
    assert await world.pending_docs(vehicle) == 10
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert final["error"] == "cap reached"
