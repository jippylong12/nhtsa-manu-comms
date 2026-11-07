"""The identity/superset check (JIP-435).

The report builder is unit-tested with fake row sets (an orphaned join row cannot be
manufactured against the real foreign key); the happy path and the below-baseline
case run against the real schema inside the rolled-back ``conn`` fixture, using
model year 2099 so no real tracked vehicle is involved.
"""

import json

import pytest

from src.scripts import identity_check as ic


def _v(id_, linked, index=0, year=2099, make="FORD", model="F 150"):
    return {"id": id_, "year": year, "make": make, "model": model, "linked": linked, "index": index}


# --- report builder (fake rows) -----------------------------------------------------


def test_a_vehicle_below_its_baseline_fails_and_is_named():
    report = ic.build_report([_v(6, 600), _v(7, 343, model="RANGER")], orphans=0, baseline={"6": 671, "7": 343})

    assert report.exit_code == 1
    by_id = {r.id: r for r in report.rows}
    assert by_id[6].status == "BELOW BASELINE" and by_id[6].delta == -71
    assert by_id[7].status == "ok" and by_id[7].delta == 0


def test_a_vehicle_above_baseline_or_without_one_passes():
    report = ic.build_report([_v(779, 160), _v(5000, 3, model="RANGER")], orphans=0, baseline={"779": 0})

    assert report.exit_code == 0
    by_id = {r.id: r for r in report.rows}
    assert by_id[779].delta == 160
    assert by_id[5000].baseline is None and by_id[5000].status == "ok"


def test_orphaned_join_rows_fail():
    report = ic.build_report([_v(6, 10)], orphans=2, baseline=None)

    assert report.exit_code == 1
    assert report.orphans == 2


def test_duplicate_active_identities_fail():
    report = ic.build_report([_v(6, 10), _v(7, 5)], orphans=0, baseline=None)

    assert report.exit_code == 1
    assert report.duplicates == [(2099, "FORD", "F 150", [6, 7])]


def test_without_a_baseline_only_orphans_and_duplicates_can_fail():
    report = ic.build_report([_v(6, 0)], orphans=0, baseline=None)

    assert report.exit_code == 0
    assert report.rows[0].baseline is None


def test_the_committed_baseline_loads():
    baseline = ic.load_baseline("tests/fixtures/identity_baseline_2026-07-24.json")

    assert baseline == {"6": 671, "7": 343, "8": 133, "779": 0, "780": 389, "1147": 8}


# --- against the real schema --------------------------------------------------------


async def _tracked(conn):
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2099, 'TESTIC', 'F 150') RETURNING id"
    )
    await conn.execute(
        "INSERT INTO comm_products (nhtsa_id, make, model, model_year, source_window) "
        "VALUES ('IC1', 'TESTIC', 'F 150', 2099, 'TESTIC'), "
        "('IC2', 'TESTIC', 'F 150', 2099, 'TESTIC')"
    )
    cid = await conn.fetchval("INSERT INTO communications (nhtsa_id) VALUES ('IC1') RETURNING id")
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid,
        vid,
    )
    return vid


@pytest.mark.asyncio
async def test_the_happy_path_exits_zero_and_counts_links_and_index(conn, capsys):
    vid = await _tracked(conn)

    code = await ic.run(conn)

    assert code == 0
    row = next(r for r in (await ic.collect(conn))[0] if r["id"] == vid)
    assert (row["linked"], row["index"]) == (1, 2)
    assert str(vid) in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_real_vehicle_below_baseline_exits_one_naming_it(conn, tmp_path, capsys):
    vid = await _tracked(conn)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({str(vid): 5}))

    code = await ic.run(conn, baseline_path=str(path))

    assert code == 1
    out = capsys.readouterr().out
    assert "BELOW BASELINE" in out and str(vid) in out


@pytest.mark.asyncio
async def test_json_output_is_machine_readable(conn, capsys):
    await _tracked(conn)

    await ic.run(conn, as_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert {"rows", "orphans", "duplicates", "exit_code"} <= payload.keys()
