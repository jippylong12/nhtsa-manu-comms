"""The one-time backfill of tracked vehicles to the plain nameplate (JIP-433).

Pure mapping rules are unit-tested on ``choose_target``; the database behaviour
(merge, dry-run, UNMAPPED, the empty-index guard) runs against the real schema
inside the rolled-back ``conn`` fixture. Test vehicles use model year 2099 with
``comm_products`` rows tagged ``source_window='TESTBF'`` so they can never
collide with a real tracked vehicle on a database that already holds some.
"""

from datetime import datetime

import pytest
import pytest_asyncio

from src.scripts import backfill_plain_nameplate as bf

YEAR = 2099


# --- choose_target: the mapping rule ------------------------------------------------


@pytest.mark.parametrize(
    "canonical, vocabulary, expected",
    [
        ("F 150 REGULAR CAB", ["F 150", "F 250"], "F 150"),
        ("F 350 SUPER DUTY", ["F 350", "F 350 SUPER DUTY"], "F 350 SUPER DUTY"),
        ("CMAX HEV", ["C MAX", "FOCUS"], "C MAX"),
        ("3 SERIES SEDAN", ["330I", "M340I"], None),
        ("MUSTANG MACH E", ["MUSTANG", "MUSTANG MACH E"], "MUSTANG MACH E"),
        ("CIVIC", ["CIVIC", "CIVIC HYBRID"], "CIVIC"),
    ],
)
def test_choose_target_applies_the_mapping_rule(canonical, vocabulary, expected):
    assert bf.choose_target(canonical, vocabulary) == expected


def test_choose_target_canonicalises_a_hyphenated_name():
    assert bf.choose_target("MUSTANG MACH-E", ["MUSTANG MACH E"]) == "MUSTANG MACH E"


# --- database behaviour -------------------------------------------------------------


async def _seed_products(conn, make, models):
    for i, model in enumerate(models):
        await conn.execute(
            "INSERT INTO comm_products (nhtsa_id, make, model, model_year, source_window) "
            "VALUES ($1, $2, $3, $4, 'TESTBF')",
            f"TESTBF{make}{i}",
            make,
            model,
            YEAR,
        )


async def _vehicle(conn, make, model, keywords=()):
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model, keywords) VALUES ($1, $2, $3, $4) RETURNING id",
        YEAR,
        make,
        model,
        list(keywords),
    )


async def _comm(conn, nhtsa_id):
    return await conn.fetchval(
        "INSERT INTO communications (nhtsa_id) VALUES ($1) RETURNING id", nhtsa_id
    )


async def _link(conn, comm_id, vehicle_id, seen, keywords=()):
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id, first_seen_at, "
        "matched_keywords) VALUES ($1, $2, $3, $4)",
        comm_id,
        vehicle_id,
        datetime.fromisoformat(seen),
        list(keywords),
    )


@pytest_asyncio.fixture
async def colliding(conn):
    """Two active vehicles that both map to FORD / F 150, three links between them."""
    await _seed_products(conn, "FORD", ["F 150"])
    a = await _vehicle(conn, "FORD", "F-150 REGULAR CAB", ["brake"])
    b = await _vehicle(conn, "FORD", "F-150 SUPER CREW", ["engine"])
    c1 = await _comm(conn, "TESTBF-C0")
    c2 = await _comm(conn, "TESTBF-C1")
    shared = await _comm(conn, "TESTBF-C2")
    await _link(conn, c1, a, "2026-01-01T00:00:00+00:00", ["brake"])
    await _link(conn, c2, b, "2026-01-02T00:00:00+00:00")
    await _link(conn, shared, a, "2026-03-01T00:00:00+00:00", ["brake"])
    await _link(conn, shared, b, "2026-02-01T00:00:00+00:00", ["engine"])
    return {"a": a, "b": b, "shared": shared}


def _mine(rows, *ids):
    return {r.id: r for r in rows if r.id in ids}


@pytest.mark.asyncio
async def test_plan_reports_a_merge_and_changes_nothing(conn, colliding):
    a, b = colliding["a"], colliding["b"]
    before = await conn.fetch("SELECT id, model, active FROM vehicles ORDER BY id")
    links_before = await conn.fetchval("SELECT count(*) FROM communication_vehicles")

    rows = _mine(await bf.plan(conn), a, b)

    assert rows[a].after == "F 150" and rows[a].action == "rename"
    assert rows[b].after == "F 150" and rows[b].action == f"merge-into {a}"
    assert (rows[a].links_before, rows[a].links_after) == (2, 3)
    assert (rows[b].links_before, rows[b].links_after) == (2, 0)
    assert before == await conn.fetch("SELECT id, model, active FROM vehicles ORDER BY id")
    assert links_before == await conn.fetchval("SELECT count(*) FROM communication_vehicles")


@pytest.mark.asyncio
async def test_apply_merges_into_the_lowest_id_with_the_union_of_links(conn, colliding):
    a, b, shared = colliding["a"], colliding["b"], colliding["shared"]

    await bf.apply(conn, await bf.plan(conn))

    survivors = await conn.fetch(
        "SELECT id, model, active, keywords FROM vehicles WHERE year=$1 AND make='FORD'", YEAR
    )
    assert [(r["id"], r["model"], r["active"]) for r in survivors] == [(a, "F 150", True)]
    assert sorted(survivors[0]["keywords"]) == ["brake", "engine"]
    assert (
        await conn.fetchval("SELECT count(*) FROM communication_vehicles WHERE vehicle_id=$1", a)
        == 3
    )
    assert (
        await conn.fetchval("SELECT count(*) FROM communication_vehicles WHERE vehicle_id=$1", b)
        == 0
    )
    shared_row = await conn.fetchrow(
        "SELECT first_seen_at, matched_keywords FROM communication_vehicles "
        "WHERE communication_id=$1 AND vehicle_id=$2",
        shared,
        a,
    )
    assert shared_row["first_seen_at"].isoformat().startswith("2026-02-01")
    assert sorted(shared_row["matched_keywords"]) == ["brake", "engine"]
    orphans = await conn.fetchval(
        "SELECT count(*) FROM communication_vehicles cv "
        "LEFT JOIN vehicles v ON v.id = cv.vehicle_id WHERE v.id IS NULL"
    )
    assert orphans == 0


@pytest.mark.asyncio
async def test_apply_moves_an_absorbed_vehicles_refresh_runs_to_the_survivor(conn, colliding):
    """refresh_runs cascades on vehicle delete; a merge must not erase its history or spend."""
    a, b = colliding["a"], colliding["b"]
    run_id = await conn.fetchval(
        "INSERT INTO refresh_runs (vehicle_id, status, actual_cost_usd) "
        "VALUES ($1, 'succeeded', 0.42) RETURNING id",
        b,
    )

    await bf.apply(conn, await bf.plan(conn))

    assert await conn.fetchval("SELECT vehicle_id FROM refresh_runs WHERE id=$1", run_id) == a


@pytest.mark.asyncio
async def test_the_unique_index_holds_after_a_merge(conn, colliding):
    await bf.apply(conn, await bf.plan(conn))

    dupes = await conn.fetchval(
        "SELECT count(*) FROM (SELECT 1 FROM vehicles WHERE active "
        "GROUP BY year, make, model HAVING count(*) > 1) d"
    )
    assert dupes == 0


@pytest.mark.asyncio
async def test_rerunning_apply_is_a_no_op(conn, colliding):
    a = colliding["a"]
    await bf.apply(conn, await bf.plan(conn))
    snapshot = await conn.fetch("SELECT id, model FROM vehicles ORDER BY id")

    rows = _mine(await bf.plan(conn), a)
    await bf.apply(conn, await bf.plan(conn))

    assert rows[a].action == "keep"
    assert snapshot == await conn.fetch("SELECT id, model FROM vehicles ORDER BY id")


@pytest.mark.asyncio
async def test_an_unmapped_vehicle_is_reported_and_left_untouched(conn):
    await _seed_products(conn, "BMW", ["330I"])
    v = await _vehicle(conn, "BMW", "3 SERIES SEDAN")

    rows = _mine(await bf.plan(conn), v)
    await bf.apply(conn, await bf.plan(conn))

    assert rows[v].action == "UNMAPPED" and rows[v].after == "3 SERIES SEDAN"
    assert await conn.fetchval("SELECT model FROM vehicles WHERE id=$1", v) == "3 SERIES SEDAN"


@pytest.mark.asyncio
async def test_apply_refuses_to_run_against_an_empty_index(conn, capsys):
    # A temp table shadows the real comm_products for this connection only.
    await conn.execute("CREATE TEMP TABLE comm_products (LIKE public.comm_products)")
    v = await _vehicle(conn, "FORD", "F-150 REGULAR CAB")

    assert await bf.run(conn, apply=True) == 2

    assert "bootstrap the index first" in capsys.readouterr().err
    assert await conn.fetchval("SELECT model FROM vehicles WHERE id=$1", v) == "F-150 REGULAR CAB"


@pytest.mark.asyncio
async def test_dry_run_exits_zero_even_with_an_unmapped_vehicle(conn):
    await _seed_products(conn, "BMW", ["330I"])
    await _vehicle(conn, "BMW", "3 SERIES SEDAN")

    assert await bf.run(conn, apply=False) == 0


# --- migration 0009 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_0009_dropped_trim_and_vehicle_catalog(conn):
    columns = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name='vehicles'"
    )
    assert "trim" not in {r["column_name"] for r in columns}
    assert await conn.fetchval("SELECT to_regclass('vehicle_catalog')") is None
