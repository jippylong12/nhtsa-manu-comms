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
    w.llm_fail = False  # True: the llm fake fails each document it picks up (a network error)
    w.embed_failed = 0  # N > 0: the embed fake embeds nothing and reports N failures

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
            if w.llm_fail:
                await conn.execute(
                    "UPDATE communications SET status='failed', status_reason=$2 WHERE id=$1",
                    r["communication_id"],
                    "LLM call failed: connection reset",
                )
                continue
            await conn.execute("UPDATE comm_documents SET llm_summary='new' WHERE id=$1", r["id"])
            await conn.execute(
                "UPDATE communications SET status='processed' WHERE id=$1", r["communication_id"]
            )
        if w.llm_cost:
            await w.ledger(w.llm_cost)
        return SimpleNamespace(
            processed=0 if w.llm_fail else len(rows),
            failed=len(rows) if w.llm_fail else 0,
            retried=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            errors=[],
        )

    async def fake_embed(limit=None, vehicle_id=None):
        w.calls.append(("embed", limit, vehicle_id))
        if w.embed_failed:
            return SimpleNamespace(
                embedded=0,
                failed=w.embed_failed,
                tokens_in=0,
                cost_usd=0.0,
                errors=["embedding service unreachable"],
            )
        rows = await conn.fetch(
            """
            UPDATE comm_documents d
            SET embedding = $2::vector, embedding_model = 'emb', embedded_at = now()
            FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE c.id = d.communication_id AND cv.vehicle_id = $1
              AND d.llm_summary IS NOT NULL AND d.embedding IS NULL
            RETURNING d.id
            """,
            vehicle_id,
            VECTOR,
        )
        return SimpleNamespace(
            embedded=len(rows), failed=0, tokens_in=0, cost_usd=0.0, errors=[]
        )

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


def test_limit_is_refused_with_reextract_before_anything_is_reset(monkeypatch):
    # --limit would only reach the extract stage, so the reset would still clear the
    # whole scope; the CLI must refuse it rather than wipe more than was asked.
    called = []

    async def fake_reextract(*args, **kwargs):
        called.append(args)
        return 0

    monkeypatch.setattr(process, "reextract", fake_reextract)
    monkeypatch.setattr("sys.argv", ["process", "--reextract", "--limit", "5", "--yes"])

    with pytest.raises(SystemExit) as exc:
        process.main()

    assert exc.value.code == 2
    assert called == []


@pytest.mark.asyncio
async def test_a_clean_run_exits_zero_and_records_ok_with_zero_failure_counts(world):
    vehicle = await world.vehicle()
    await world.comm(vehicle)

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    assert code == 0
    final = await world.last_run("reextract")
    assert final["ok"] is True
    assert final["error"] is None
    counts = json.loads(final["counts"])
    assert (counts["failed"], counts["without_summary"], counts["unembedded"]) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_stage_that_reports_embed_failures_makes_the_run_exit_one_and_not_ok(world):
    vehicle = await world.vehicle()
    await world.comm(vehicle)
    world.embed_failed = 3

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    assert code == 1
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert "embed failed=3" in final["error"]
    assert "without an embedding" in final["error"]
    assert json.loads(final["counts"])["unembedded"] == 1


@pytest.mark.asyncio
async def test_communications_left_failed_by_the_llm_stage_make_the_run_exit_one(world):
    vehicle = await world.vehicle()
    comm = await world.comm(vehicle)
    world.llm_fail = True

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None)

    assert code == 1
    assert (await world.comm_row(comm))["status"] == "failed"
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert "llm failed=1" in final["error"]
    assert "1 communications failed" in final["error"]
    assert "1 documents without a summary" in final["error"]
    counts = json.loads(final["counts"])
    assert (counts["failed"], counts["without_summary"]) == (1, 1)


@pytest.mark.asyncio
async def test_resume_skips_the_reset_requeues_a_network_failure_and_runs_the_stages(world):
    vehicle = await world.vehicle()
    kept = await world.comm(vehicle)
    stranded = await world.comm(
        vehicle, status="failed", summarised=False, reason="pdf unavailable: timed out"
    )

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None, resume=True)

    assert code == 0
    assert await world.last_run("reextract-reset") is None, "a resume resets nothing"
    kept_doc = (await world.doc_rows(kept))[0]
    assert kept_doc["llm_summary"] == "summary"
    assert kept_doc["embedding"] is not None
    assert (await world.comm_row(kept))["status"] == "processed"
    requeued = await world.comm_row(stranded)
    assert (requeued["status"], requeued["status_reason"], requeued["attempts"]) == (
        "processed",  # re-queued to pending, then read by the fake llm stage
        None,
        0,
    )
    assert (await world.doc_rows(stranded))[0]["llm_summary"] == "new"
    assert [c[0] for c in world.calls] == ["extract", "llm", "embed", "prune"]
    final = await world.last_run("reextract")
    assert final["ok"] is True
    assert json.loads(final["counts"])["requeued"] == 1


@pytest.mark.asyncio
async def test_resume_leaves_a_schema_violation_failed_and_exits_one(world):
    vehicle = await world.vehicle()
    violation = await world.comm(
        vehicle, status="failed", summarised=False, reason="LLM schema violation: no remedy"
    )

    code = await process.reextract(vehicle, cap_usd=5.0, sync=True, limit=None, resume=True)

    assert code == 1
    row = await world.comm_row(violation)
    assert (row["status"], row["status_reason"], row["attempts"]) == (
        "failed",
        "LLM schema violation: no remedy",
        2,
    )
    assert "llm" not in [c[0] for c in world.calls]
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert "1 communications failed" in final["error"]
    assert json.loads(final["counts"])["requeued"] == 0


def test_resume_is_refused_without_reextract(monkeypatch):
    monkeypatch.setattr("sys.argv", ["process", "--resume", "--yes"])

    with pytest.raises(SystemExit) as exc:
        process.main()

    assert exc.value.code == 2


def _patch_batch_path(monkeypatch, world, *, per_doc_cost=0.001, collect_spend=0.0):
    """Fake the Batch API calls and the price estimate; records how they were called."""
    rec = SimpleNamespace(submits=[], collects=[])

    async def cost_per_document():
        return per_doc_cost

    async def fake_submit(limit=None, vehicle_id=None):
        rec.submits.append((limit, vehicle_id))
        rows = await world.conn.fetch(
            """
            SELECT d.id, d.communication_id FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE cv.vehicle_id = $1 AND d.llm_summary IS NULL AND c.status = 'pending'
            """,
            vehicle_id,
        )
        for r in rows:  # the batch "ends" with every document read, as collect would store them
            await world.conn.execute(
                "UPDATE comm_documents SET llm_summary='new' WHERE id=$1", r["id"]
            )
            await world.conn.execute(
                "UPDATE communications SET status='processed' WHERE id=$1", r["communication_id"]
            )
        return ["msgbatch_test"]

    async def fake_collect(batch_ids, poll_seconds=30, max_wait=3600):
        rec.collects.append((list(batch_ids), poll_seconds, max_wait))
        if collect_spend:
            await world.ledger(collect_spend)
        return SimpleNamespace(
            processed=0, failed=0, tokens_in=0, tokens_out=0, cost_usd=0.0, errors=[]
        )

    monkeypatch.setattr("src.refresh.estimate.cost_per_document", cost_per_document)
    monkeypatch.setattr(llm_stage, "submit_batch", fake_submit)
    monkeypatch.setattr(llm_stage, "collect_batch", fake_collect)
    return rec


@pytest.mark.asyncio
async def test_the_batch_path_submits_everything_once_and_collects_with_a_24_hour_wait(
    world, monkeypatch
):
    vehicle = await world.vehicle()
    for _ in range(75):
        await world.comm(vehicle, status="pending", summarised=False)
    batch = _patch_batch_path(monkeypatch, world)

    code = await process.reextract(vehicle, cap_usd=5.0, sync=False, limit=None)

    assert code == 0
    assert batch.submits == [(None, vehicle)]
    assert len(batch.collects) == 1
    ids, poll_seconds, max_wait = batch.collects[0]
    assert (ids, poll_seconds) == (["msgbatch_test"], 60)
    assert max_wait >= 86400
    assert "llm" not in [c[0] for c in world.calls], "no 50-document slices on the batch path"
    assert [c[0] for c in world.calls] == ["extract", "embed", "prune"]


@pytest.mark.asyncio
async def test_a_batch_estimate_at_or_over_the_cap_stops_before_submitting(world, monkeypatch):
    vehicle = await world.vehicle()
    for _ in range(2):
        await world.comm(vehicle, status="pending", summarised=False)
    # 2 documents x $1.00 / 2 (batch pricing) = $1.00 estimate against a $1.00 cap
    batch = _patch_batch_path(monkeypatch, world, per_doc_cost=1.0)

    code = await process.reextract(vehicle, cap_usd=1.0, sync=False, limit=None)

    assert code == 3
    assert batch.submits == [] and batch.collects == []
    assert await world.pending_docs(vehicle) == 2
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert final["error"] == "cap reached"


@pytest.mark.asyncio
async def test_a_ledger_over_the_cap_after_the_batch_is_collected_exits_three(world, monkeypatch):
    vehicle = await world.vehicle()
    await world.comm(vehicle, status="pending", summarised=False)
    batch = _patch_batch_path(monkeypatch, world, collect_spend=6.0)

    code = await process.reextract(vehicle, cap_usd=5.0, sync=False, limit=None)

    assert code == 3
    assert len(batch.collects) == 1
    assert "embed" not in [c[0] for c in world.calls]
    final = await world.last_run("reextract")
    assert final["ok"] is False
    assert final["error"] == "cap reached"
