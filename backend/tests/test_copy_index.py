"""copy_index: export the lean flat-file index, apply it to another database wholesale.

Runs inside the rolled-back ``conn`` fixture. The "destination" is the same database after
the four index tables are emptied and a tracked vehicle's identity is set back to a pre-re-key
spelling, which is what production looked like when the rebuild there timed out.
"""

import json

import pytest

from src.scripts import copy_index as ci

pytestmark = pytest.mark.asyncio


async def _snapshot(conn):
    out = {}
    for t, order in (
        ("flatfile_ingests", "source_window"),
        ("comm_vocab", "source_window, model_year, make, model"),
        ("vehicle_scans", "vehicle_id, source_window"),
        ("vehicle_bulletins", "vehicle_id, nhtsa_id"),
    ):
        out[t] = [tuple(r) for r in await conn.fetch(f"SELECT * FROM {t} ORDER BY {order}")]
    return out


async def test_dry_run_writes_nothing_and_apply_restores_the_index_exactly(conn):
    if not await conn.fetchval("SELECT count(*) FROM comm_vocab"):
        pytest.skip("replica index not bootstrapped")
    before = await _snapshot(conn)
    payload = json.loads(json.dumps(await ci.export_index(conn)))
    vid = await conn.fetchval("SELECT id FROM vehicles WHERE active ORDER BY id LIMIT 1")
    original_model = await conn.fetchval("SELECT model FROM vehicles WHERE id=$1", vid)

    # the destination: empty index tables, one vehicle still on its old spelling
    await conn.execute("TRUNCATE flatfile_ingests, comm_vocab, vehicle_scans, vehicle_bulletins")
    await conn.execute("UPDATE vehicles SET model = 'OLD SPELLING X' WHERE id=$1", vid)

    report = await ci.apply_index(conn, payload, apply=False)

    assert report.counts["comm_vocab"] == len(payload["comm_vocab"])
    assert await conn.fetchval("SELECT count(*) FROM comm_vocab") == 0, "dry run rolled back"
    assert await conn.fetchval("SELECT model FROM vehicles WHERE id=$1", vid) == "OLD SPELLING X"

    report = await ci.apply_index(conn, payload, apply=True)

    assert await _snapshot(conn) == before
    assert await conn.fetchval("SELECT model FROM vehicles WHERE id=$1", vid) == original_model
    assert report.vehicles_updated == len(payload["vehicles"])
    assert report.skipped_vehicles == []


async def test_a_source_vehicle_missing_at_the_destination_is_skipped_with_its_rows(conn):
    if not await conn.fetchval("SELECT count(*) FROM comm_vocab"):
        pytest.skip("replica index not bootstrapped")
    payload = json.loads(json.dumps(await ci.export_index(conn)))
    ghost = 999_999_001
    payload["vehicles"].append(
        {"id": ghost, "year": 2099, "make": "GHOST", "model": "X", "keywords": [], "active": True}
    )
    payload["vehicle_scans"].append(
        {"vehicle_id": ghost, "source_window": "W", "etag": "e", "scanned_at": None}
    )
    payload["vehicle_bulletins"].append([ghost, "GHOST-1", "", "W"])

    report = await ci.apply_index(conn, payload, apply=False)

    assert report.skipped_vehicles == [f"vehicle {ghost} (2099 GHOST X)"]
    assert report.dropped_rows == 2
    assert report.counts["vehicle_scans"] == len(payload["vehicle_scans"]) - 1


async def test_a_destination_without_the_index_tables_is_refused(conn):
    payload = {"schema_version": 1, "vehicles": [], "vehicle_scans": [], "vehicle_bulletins": [],
               "comm_vocab": [], "flatfile_ingests": []}
    await conn.execute("ALTER TABLE comm_vocab RENAME TO comm_vocab_gone")
    try:
        with pytest.raises(ci.PreconditionError) as exc:
            await ci.apply_index(conn, payload, apply=False)
        assert "comm_vocab" in str(exc.value)
    finally:
        await conn.execute("ALTER TABLE comm_vocab_gone RENAME TO comm_vocab")
