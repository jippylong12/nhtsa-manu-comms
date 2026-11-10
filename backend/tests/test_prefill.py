"""The prefill CLI (JIP-1586): create the seed vehicles, sync them, process them under a cap.

Runs against the test database. Sync, the three processor stages and `prune_archive` are
monkeypatched to record their calls (and to write fake ledger rows), so nothing here calls a
model, Batch, Gemini or NHTSA. Vehicles use the make `TESTPREFILL` and are removed afterwards.
"""

import json
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from src.communications import flatfile
from src.config import get_settings
from src.jobs import extract as extract_stage
from src.jobs import llm as llm_stage
from src.jobs import process
from src.jobs import sync as sync_job
from src.scripts import prefill

MAKE = "TESTPREFILL"


class World:
    def __init__(self, conn, tmp_path):
        self.conn = conn
        self.tmp_path = tmp_path
        self.calls: list[tuple] = []
        self.baseline_run = 0
        self.unresolved: list[str] = []  # batches an earlier, cut-off run left out
        self.ledger_in_extract = 0.0  # a fake ledger row written when the extract stage runs

    def list_file(self, n: int = 2) -> str:
        entries = [{"year": 2099, "make": MAKE, "model": f"M{i}"} for i in range(1, n + 1)]
        path = self.tmp_path / "list.json"
        path.write_text(json.dumps(entries))
        return str(path)

    async def vehicle_ids(self) -> list[int]:
        rows = await self.conn.fetch("SELECT id FROM vehicles WHERE make = $1 ORDER BY id", MAKE)
        return [r["id"] for r in rows]

    async def vehicle_count(self) -> int:
        return await self.conn.fetchval("SELECT count(*) FROM vehicles")

    async def run_row(self, run: str) -> dict | None:
        """The latest pipeline_runs row of a prefill run (labelled by counts.run)."""
        row = await self.conn.fetchrow(
            "SELECT * FROM pipeline_runs WHERE counts->>'run' = $1 AND id > $2 "
            "ORDER BY id DESC LIMIT 1",
            run,
            self.baseline_run,
        )
        if row is None:
            return None
        d = dict(row)
        d["counts"] = json.loads(d["counts"]) if isinstance(d["counts"], str) else d["counts"]
        return d


@pytest_asyncio.fixture
async def world(monkeypatch, tmp_path):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    w = World(conn, tmp_path)
    w.baseline_run = await conn.fetchval("SELECT coalesce(max(id), 0) FROM pipeline_runs")

    async def fake_scan(c, vehicles):
        w.calls.append(("scan", tuple(vehicles)))
        return {}

    async def fake_sync(vehicle_ids, limit):
        w.calls.append(("sync", tuple(vehicle_ids), limit))
        return sync_job.SyncStats(found=3, new_comms=2, new_docs=2)

    async def fake_extract(limit, vehicle_id=None):
        w.calls.append(("extract", limit, vehicle_id))
        if w.ledger_in_extract:
            await conn.execute(
                "INSERT INTO usage_events (kind, cost_usd, meta) VALUES "
                "('llm_extract', $1, '{\"ns\": \"TESTPREFILL\"}'::jsonb)",
                w.ledger_in_extract,
            )
        return SimpleNamespace(failed=0)

    async def fake_llm(vehicle_id, cap_usd, started_at, per_doc):
        w.calls.append(("llm", vehicle_id))
        return None, 0

    async def fake_embed(limit, vehicle_id=None):
        w.calls.append(("embed", limit, vehicle_id))
        return SimpleNamespace(failed=0)

    async def fake_prune(dry_run=False):
        w.calls.append(("prune",))
        return 0, 0

    async def unresolved(conn):
        return list(w.unresolved)

    async def fake_collect(batch_ids, poll_seconds=30, max_wait=3600):
        w.calls.append(("collect", tuple(batch_ids)))
        return SimpleNamespace(
            unfinished=[], processed=1, failed=0, tokens_in=0, tokens_out=0, cost_usd=0.0
        )

    monkeypatch.setattr(process, "_unresolved_batches", unresolved)
    monkeypatch.setattr(llm_stage, "collect_batch", fake_collect)
    monkeypatch.setattr(flatfile, "refresh_index", fake_scan)
    monkeypatch.setattr(sync_job, "run", fake_sync)
    monkeypatch.setattr(process, "run_extract", fake_extract)
    monkeypatch.setattr(process, "_batch_llm_once", fake_llm)
    monkeypatch.setattr(process, "run_embed", fake_embed)
    monkeypatch.setattr(extract_stage, "prune_archive", fake_prune)
    try:
        yield w
    finally:
        await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)
        await conn.execute("DELETE FROM usage_events WHERE meta->>'ns' = $1", MAKE)
        await conn.execute("DELETE FROM pipeline_runs WHERE id > $1", w.baseline_run)
        await conn.close()


@pytest.mark.asyncio
async def test_dry_run_lists_the_fifty_seed_identities_with_an_estimate_and_writes_nothing(
    world, capsys
):
    before = await world.vehicle_count()

    code = await prefill.run(["--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.count("\n  20") == 50  # one indented "year make model" line per identity
    assert "FORD F 150" in out and "NISSAN ROGUE" in out
    assert "estimated" in out.lower() and "$" in out
    assert await world.vehicle_count() == before
    assert world.calls == []
    assert await world.run_row("prefill-sync") is None


@pytest.mark.asyncio
async def test_apply_creates_the_vehicles_syncs_exactly_them_and_runs_the_stages_in_order(world):
    path = world.list_file(3)

    code = await prefill.run(["--list", path, "--only", "2", "--apply", "--yes"])

    ids = await world.vehicle_ids()
    assert code == 0
    assert len(ids) == 2
    syncs = [c for c in world.calls if c[0] == "sync"]
    assert syncs == [("sync", tuple(ids), None)]
    # One scan call for all the vehicles: each flat-file window is read once, not once per vehicle.
    assert [c for c in world.calls if c[0] == "scan"] == [("scan", tuple(ids))]
    stages = [(c[0], c[-1]) for c in world.calls if c[0] in ("extract", "llm", "embed")]
    assert stages == [(s, v) for s in ("extract", "llm", "embed") for v in ids]
    kinds = [c[0] for c in world.calls]
    assert kinds.index("sync") < kinds.index("extract") and kinds[-1] == "prune"
    sync_run = await world.run_row("prefill-sync")
    assert sync_run["counts"]["created"] == 2 and sync_run["counts"]["vehicle_ids"] == ids
    assert (await world.run_row("prefill-process"))["ok"] is True


@pytest.mark.asyncio
async def test_spend_past_the_cap_stops_before_the_llm_stage_records_it_and_exits_3(world):
    world.ledger_in_extract = 2.0  # each extract call writes a fake ledger row above the cap
    path = world.list_file(2)

    code = await prefill.run(["--list", path, "--apply", "--yes", "--cap-usd", "1"])

    assert code == 3
    kinds = [c[0] for c in world.calls]
    assert "extract" in kinds
    assert "llm" not in kinds and "embed" not in kinds and "prune" not in kinds
    row = await world.run_row("prefill-process")
    assert row["ok"] is False and row["error"] == "cap reached"
    assert float(row["cost_usd"]) == pytest.approx(4.0)  # two extract calls


@pytest.mark.asyncio
async def test_running_again_reuses_the_vehicles_and_creates_no_duplicates(world):
    path = world.list_file(2)
    await prefill.run(["--list", path, "--apply", "--yes"])
    first = await world.vehicle_ids()
    world.calls.clear()

    code = await prefill.run(["--list", path, "--apply", "--yes"])

    assert code == 0
    assert await world.vehicle_ids() == first
    assert [c for c in world.calls if c[0] == "sync"] == [("sync", tuple(first), None)]
    again = await world.run_row("prefill-sync")
    assert again["counts"]["created"] == 0 and again["counts"]["existing"] == 2


@pytest.mark.asyncio
async def test_a_batch_left_out_by_an_earlier_run_is_collected_before_anything_new_is_sent(world):
    world.unresolved = ["msgbatch_left_out"]

    code = await prefill.run(["--list", world.list_file(2), "--apply", "--yes"])

    kinds = [c[0] for c in world.calls]
    assert code == 0
    assert world.calls[0] == ("collect", ("msgbatch_left_out",))
    assert kinds.index("collect") < kinds.index("sync") < kinds.index("llm")
