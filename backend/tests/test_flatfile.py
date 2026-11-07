"""The flat-file index: normalization, CSV hygiene, and ETag-driven ingest.

The picker reads `comm_vocab` and sync reads `vehicle_bulletins`, and both are only
ever as correct as this ingest and scan. The unit cases pin the normalization vocabulary and the row
hygiene JIP-428 specified (comma-packed years, 9999, junk `Model == Make` rows,
hyphen/space variants); the DB cases pin the two behaviours the daily refresh
depends on -- a window is re-ingested only when its ETag changed or a vehicle has
not been scanned against it, and re-ingest replaces the window's rows rather than
duplicating them.

The HTTP layer is monkeypatched so the ingest path runs end to end without
downloading NHTSA's multi-megabyte zips; a real download is exercised once,
out of band, by the M3 smoke run.
"""

import asyncio

import asyncpg
import pytest

from src.communications import flatfile
from src.config import get_settings

# --------------------------------------------------------------------------
# Normalization (ADR 0001 section 5)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("F-150", "F 150"),
        ("MUSTANG MACH-E", "MUSTANG MACH E"),
        ("  C-MAX  ", "C MAX"),
        ("4500 HD", "4500 HD"),
        ("SILVERADO 1500", "SILVERADO 1500"),
        ("f-150 lightning bev", "F 150 LIGHTNING BEV"),
        ("F--150   SUPER", "F 150 SUPER"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_model(raw, expected):
    assert flatfile.normalize_model(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("C-MAX", "CMAX"),
        ("MACH E", "MACHE"),
        ("4500 HD", "4500HD"),
        ("SILVERADO 1500", "SILVERADO1500"),
    ],
)
def test_compact_model(raw, expected):
    """The compact form is the M4 backfill's fallback matcher; it must collapse
    exactly the space/hyphen differences the canonical form leaves behind."""
    assert flatfile.compact_model(raw) == expected


# --------------------------------------------------------------------------
# CSV hygiene
# --------------------------------------------------------------------------


def test_parse_rows_applies_every_hygiene_rule():
    lines = [
        '"TSB/Document ID","Make","Model","Model Year","Concise Summary"',  # header
        '"11011217","KIA","FORTE","2017,2018","engine campaign"',  # comma years -> 2 rows
        '"10127404","FORD","F-150","2015,2016","trailer"',  # hyphen -> "F 150", 2 rows
        '"999","BMW","BMW","2020","make==model junk"',  # dropped
        '"888","TRIUMPH","Triumph","2021","case-insensitive junk"',  # dropped
        '"777","HONDA","CIVIC","9999","unknown year kept"',  # 9999 kept
        '"666","FORD","","2020","missing model"',  # dropped
        '"444","FORD","RANGER","","missing year"',  # dropped
    ]
    rows = list(flatfile.parse_rows(lines))

    assert rows == [
        ("11011217", "KIA", "FORTE", 2017, "engine campaign"),
        ("11011217", "KIA", "FORTE", 2018, "engine campaign"),
        ("10127404", "FORD", "F 150", 2015, "trailer"),
        ("10127404", "FORD", "F 150", 2016, "trailer"),
        ("777", "HONDA", "CIVIC", 9999, "unknown year kept"),
    ]


def test_parse_rows_handles_embedded_commas_and_quotes():
    """A summary carries commas and doubled quotes; the CSV reader, not a naive
    split, has to own the parse or the id/make columns would shift."""
    lines = [
        '"10500000","FORD","EXPLORER","2022","Some vehicles may exhibit a ""fault"" message, with codes."',
    ]
    rows = list(flatfile.parse_rows(lines))
    assert rows == [
        (
            "10500000",
            "FORD",
            "EXPLORER",
            2022,
            'Some vehicles may exhibit a "fault" message, with codes.',
        ),
    ]


# --------------------------------------------------------------------------
# Ingest, scan and ETag idempotency (against the real test database)
# --------------------------------------------------------------------------

_WIN = "TESTWIN"

_CSV = [
    '"TSB/Document ID","Make","Model","Model Year","Concise Summary"',
    '"700001","HONDA","CIVIC","2020,2021","summary a"',
    '"700002","HONDA","CIVIC","9999","summary b"',
]

# A made-up make, so the vocabulary the aliases are folded from is exactly this slice.
_ALIAS_CSV = [
    '"TSB/Document ID","Make","Model","Model Year","Concise Summary"',
    '"710001","TESTMAKE","F 250 SD","2020","real a"',
    '"710002","TESTMAKE","F 250 SD","2020","real b"',
    '"710002","TESTMAKE","REDUNDANT F 250","2020","same bulletin, junk spelling"',
    '"710003","TESTMAKE","REDUNDANT F 250","2020","filed only under the junk spelling"',
    '"710009","TESTMAKE","RANGER","2020","another nameplate"',
]


async def _vehicle(conn, year=2020, make="HONDA", model="CIVIC") -> dict:
    """The tracked vehicle with this identity (reusing a real one if the replica has it)."""
    vid = await conn.fetchval(
        "SELECT id FROM vehicles WHERE active AND year=$1 AND make=$2 AND model=$3",
        year,
        make,
        model,
    ) or await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
        year,
        make,
        model,
    )
    return {"id": vid, "year": year, "make": make, "model": model}


async def _vocab(conn):
    return [
        tuple(r)
        for r in await conn.fetch(
            "SELECT model_year, make, model, bulletins FROM comm_vocab "
            "WHERE source_window = $1 ORDER BY make, model, model_year",
            _WIN,
        )
    ]


async def _bulletins(conn, vehicle_id):
    """The vehicle's bulletins this test's window wrote (a real tracked vehicle can
    already hold bulletins from the real windows; those are not under test)."""
    return [
        tuple(r)
        for r in await conn.fetch(
            "SELECT nhtsa_id, summary FROM vehicle_bulletins "
            "WHERE vehicle_id = $1 AND source_window = $2 ORDER BY nhtsa_id",
            vehicle_id,
            _WIN,
        )
    ]


class _Http:
    """The HTTP layer, monkeypatched to serve ``csv`` under ``etag``."""

    def __init__(self, csv):
        self.csv = csv
        self.etag = "E1"
        self.downloads = 0


@pytest.fixture
def http(monkeypatch, tmp_path):
    """Head, download and zip parsing patched; the cache points into tmp_path."""
    monkeypatch.setenv("FLATFILE_CACHE_DIR", str(tmp_path / "cache"))
    state = _Http(_CSV)

    async def fake_head(_client, _window):
        return state.etag, None

    async def fake_download(_client, _window, dest):
        state.downloads += 1
        dest.write(b"PK")
        return state.etag, None

    monkeypatch.setattr(flatfile, "head_window", fake_head)
    monkeypatch.setattr(flatfile, "_download_window", fake_download)
    monkeypatch.setattr(flatfile, "_iter_csv_lines", lambda _path: iter(state.csv))
    return state


@pytest.mark.asyncio
async def test_ingest_window_builds_the_vocabulary_and_the_vehicles_bulletins(conn):
    vehicle = await _vehicle(conn)

    report = await flatfile.ingest_window(
        conn,
        _WIN,
        flatfile.parse_rows(_CSV),
        etag="E1",
        last_modified=None,
        vehicles=[vehicle],
    )

    # 700001 -> (2020, 2021), 700002 -> (9999): three product rows.
    assert report["rows"] == 3
    assert report["vocab_rows"] == 3
    assert report["per_vehicle"] == {vehicle["id"]: 1}
    assert await _vocab(conn) == [
        (2020, "HONDA", "CIVIC", 1),
        (2021, "HONDA", "CIVIC", 1),
        (9999, "HONDA", "CIVIC", 1),
    ]
    # The vehicle is (2020, HONDA, CIVIC): only 700001 lists that year.
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]
    scan = await conn.fetchrow(
        "SELECT etag FROM vehicle_scans WHERE vehicle_id = $1 AND source_window = $2",
        vehicle["id"],
        _WIN,
    )
    assert scan["etag"] == "E1"
    recorded = await conn.fetchrow(
        "SELECT etag, row_count FROM flatfile_ingests WHERE source_window = $1", _WIN
    )
    assert (recorded["etag"], recorded["row_count"]) == ("E1", 3)


@pytest.mark.asyncio
async def test_refresh_skips_a_window_whose_etag_is_unchanged_and_fully_scanned(conn, http):
    vehicle = await _vehicle(conn)
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) VALUES ($1, 'E1', 3)",
        _WIN,
    )
    await conn.execute(
        "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) VALUES ($1, $2, 'E1')",
        vehicle["id"],
        _WIN,
    )

    report = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )

    assert report[_WIN]["status"] == "unchanged"
    assert report[_WIN]["scanned"] == []
    assert http.downloads == 0, "an unchanged ETag must not trigger a download"
    assert await _vocab(conn) == []


@pytest.mark.asyncio
async def test_refresh_reingests_a_window_whose_etag_changed(conn, http):
    vehicle = await _vehicle(conn)
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) VALUES ($1, 'E1', 0)",
        _WIN,
    )
    http.etag = "E2"

    report = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )

    assert report[_WIN]["status"] == "ingested"
    assert report[_WIN]["rows"] == 3
    assert report[_WIN]["scanned"] == [vehicle["id"]]
    assert len(await _vocab(conn)) == 3
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]
    assert (
        await conn.fetchval("SELECT etag FROM flatfile_ingests WHERE source_window=$1", _WIN)
        == "E2"
    )


@pytest.mark.asyncio
async def test_reingest_replaces_rather_than_duplicates(conn, http):
    """A window ingested twice must leave exactly one copy of each row."""
    vehicle = await _vehicle(conn)
    http.etag = "E2"

    await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]], force=True
    )
    await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]], force=True
    )

    assert len(await _vocab(conn)) == 3
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]
    assert (
        await conn.fetchval("SELECT count(*) FROM vehicle_scans WHERE source_window=$1", _WIN) >= 1
    )


@pytest.mark.asyncio
async def test_a_vehicle_with_no_scan_is_scanned_even_when_the_window_is_unchanged(conn, http):
    vehicle = await _vehicle(conn)
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) VALUES ($1, 'E1', 3)",
        _WIN,
    )

    report = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )

    assert report[_WIN]["status"] == "ingested", "an unscanned vehicle makes the window stale"
    assert report[_WIN]["scanned"] == [vehicle["id"]]
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]


@pytest.mark.asyncio
async def test_a_second_refresh_with_the_same_etag_does_nothing(conn, http):
    vehicle = await _vehicle(conn)
    first = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )
    assert first[_WIN]["status"] == "ingested"
    assert http.downloads == 1

    second = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )

    assert second[_WIN]["status"] == "unchanged"
    assert second[_WIN]["scanned"] == []
    assert http.downloads == 1, "no second download"


@pytest.mark.asyncio
async def test_a_scan_folds_aliases_into_the_vehicles_bulletins(conn, http):
    vehicle = await _vehicle(conn, make="TESTMAKE", model="F 250 SD")
    ranger = await _vehicle(conn, make="TESTMAKE", model="RANGER")
    http.csv = _ALIAS_CSV

    await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"], ranger["id"]]
    )

    assert await _bulletins(conn, vehicle["id"]) == [
        ("710001", "real a"),
        ("710002", "real b"),  # under both spellings: one bulletin, the vehicle's own summary
        ("710003", "filed only under the junk spelling"),
    ]
    assert await _bulletins(conn, ranger["id"]) == [("710009", "another nameplate")]


@pytest.mark.asyncio
async def test_a_bulletin_nhtsa_withdraws_disappears_on_the_next_scan(conn, http):
    vehicle = await _vehicle(conn)
    http.etag = "E1"
    await flatfile.refresh_index(conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]])
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]

    http.etag = "E2"
    http.csv = [_CSV[0], '"700005","HONDA","CIVIC","2020","summary e"']
    await flatfile.refresh_index(conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]])

    assert await _bulletins(conn, vehicle["id"]) == [("700005", "summary e")]


@pytest.mark.asyncio
async def test_scan_vehicle_scans_every_window_and_vehicle_is_scanned_reports_it(
    conn, http, monkeypatch
):
    vehicle = await _vehicle(conn)

    async def one_window(_client):
        return [_WIN]

    monkeypatch.setattr(flatfile, "discover_windows", one_window)
    # _vehicle reuses the replica's real 2020 Civic when it is tracked, and a
    # bootstrapped replica has already scanned it: start from "never scanned".
    await conn.execute("DELETE FROM vehicle_scans WHERE vehicle_id = $1", vehicle["id"])
    assert await flatfile.vehicle_is_scanned(conn, vehicle["id"]) is False

    report = await flatfile.scan_vehicle(conn, vehicle["id"])

    assert report[_WIN]["scanned"] == [vehicle["id"]]
    assert await flatfile.vehicle_is_scanned(conn, vehicle["id"]) is True
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]


# --------------------------------------------------------------------------
# The zip cache: backend/.flatfile_cache/<window>.<etag>.zip
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cached_zip_for_the_current_etag_is_parsed_without_a_download(
    conn, http, monkeypatch, tmp_path
):
    vehicle = await _vehicle(conn)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{_WIN}.E1.zip").write_bytes(b"PK")

    async def no_download(*_a, **_kw):
        raise AssertionError("a cache hit must not download")

    monkeypatch.setattr(flatfile, "_download_window", no_download)

    report = await flatfile.refresh_index(
        conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]]
    )

    assert report[_WIN]["status"] == "ingested"
    assert await _bulletins(conn, vehicle["id"]) == [("700001", "summary a")]


@pytest.mark.asyncio
async def test_two_downloads_of_one_window_at_once_both_land_one_whole_cached_zip(
    monkeypatch, tmp_path
):
    """Scan on add and a refresh run can download the same window together; a shared
    ``.part`` file let them interleave writes and rename it out from under each other."""
    monkeypatch.setenv("FLATFILE_CACHE_DIR", str(tmp_path / "cache"))
    chunk = 64 * 1024  # larger than the file buffer, so every write reaches the file

    async def slow_download(_client, _window, dest):
        fill = b"A" if not getattr(slow_download, "started", False) else b"B"
        slow_download.started = True
        for _ in range(4):
            dest.write(fill * chunk)
            await asyncio.sleep(0)
        dest.flush()
        return '"E9"', None

    monkeypatch.setattr(flatfile, "_download_window", slow_download)

    results = await asyncio.gather(
        flatfile._window_zip(object(), _WIN, None), flatfile._window_zip(object(), _WIN, None)
    )

    cache = tmp_path / "cache"
    assert [p.name for p in cache.iterdir()] == [f"{_WIN}.E9.zip"]
    assert all(path == cache / f"{_WIN}.E9.zip" for path, *_ in results)
    data = (cache / f"{_WIN}.E9.zip").read_bytes()
    assert data in (b"A" * chunk * 4, b"B" * chunk * 4)


@pytest.mark.asyncio
async def test_a_window_pass_waits_for_another_connections_pass_over_the_same_window(conn):
    """Two passes over one window (scan on add vs a refresh run) must take turns: run
    together, the second one's comm_vocab insert hit the first's primary key."""
    vehicle = await _vehicle(conn)
    other = await asyncpg.connect(get_settings().database_url)
    try:
        await other.execute("SELECT pg_advisory_lock($1::bigint)", flatfile._window_lock_key(_WIN))
        task = asyncio.create_task(
            flatfile.ingest_window(
                conn,
                _WIN,
                flatfile.parse_rows(_CSV),
                etag="E1",
                last_modified=None,
                vehicles=[vehicle],
            )
        )
        await asyncio.sleep(0.5)
        assert not task.done(), "the pass must wait while another holds the window"
        await other.execute(
            "SELECT pg_advisory_unlock($1::bigint)", flatfile._window_lock_key(_WIN)
        )
        report = await asyncio.wait_for(task, 10)
    finally:
        await other.close()

    assert report["per_vehicle"][vehicle["id"]] >= 1


@pytest.mark.asyncio
async def test_a_new_etag_downloads_and_removes_the_old_cached_zip(conn, http, tmp_path):
    vehicle = await _vehicle(conn)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{_WIN}.OLD.zip").write_bytes(b"PK")
    (cache / "OTHERWIN.OLD.zip").write_bytes(b"PK")
    http.etag = '"E/3"'  # S3 sends the quotes; the slash must not escape the cache dir

    await flatfile.refresh_index(conn, client=object(), windows=[_WIN], vehicles=[vehicle["id"]])

    assert http.downloads == 1
    assert sorted(p.name for p in cache.iterdir()) == ["OTHERWIN.OLD.zip", f"{_WIN}.E3.zip"]
    assert (
        await conn.fetchval("SELECT etag FROM flatfile_ingests WHERE source_window=$1", _WIN)
        == '"E/3"'
    )


# --------------------------------------------------------------------------
# Name aliasing: one resolver for the picker AND for matching (resolve_models)
# --------------------------------------------------------------------------


def test_spacing_variants_resolve_to_the_spelling_with_more_bulletins():
    resolved = flatfile.resolve_models({"F 53": 41, "F53": 2, "E350": 9, "E 350": 3})
    assert resolved == {"F 53": "F 53", "F53": "F 53", "E350": "E350", "E 350": "E350"}


def test_a_spacing_tie_keeps_the_spelling_with_more_spaces():
    resolved = flatfile.resolve_models({"GT40": 5, "GT 40": 5})
    assert resolved == {"GT40": "GT 40", "GT 40": "GT 40"}


def test_a_junk_name_resolves_to_the_real_spelling_with_the_same_key():
    resolved = flatfile.resolve_models(
        {"ESCAPE": 200, "REDUNDANT ESCAPE": 2, "EDGE": 9, "DUPLICATE EDGE": 1, "RANGER": 4}
    )
    assert resolved["REDUNDANT ESCAPE"] == "ESCAPE"
    assert resolved["DUPLICATE EDGE"] == "EDGE"
    assert resolved["RANGER"] == "RANGER"


def test_a_junk_name_follows_a_folded_spacing_variant_to_the_preferred_spelling():
    resolved = flatfile.resolve_models({"F 53": 41, "F53": 2, "REDUNDANT F53": 1})
    assert resolved["REDUNDANT F53"] == "F 53"


def test_a_junk_name_with_no_same_key_sibling_resolves_to_the_unique_prefix_or_suffix_match():
    resolved = flatfile.resolve_models(
        {
            "F 150": 600,
            "F 250 SD": 240,
            "F 350 SD": 240,
            "REDUNDANT F 250": 5,
            "MUSTANG MACH E": 330,
            "MUSTANG": 150,
            "REDUNDANT MACH E": 5,
        }
    )
    assert resolved["REDUNDANT F 250"] == "F 250 SD", "key F250 starts F250SD, and only that"
    assert resolved["REDUNDANT MACH E"] == "MUSTANG MACH E", "key MACHE ends MUSTANGMACHE"


def test_a_junk_name_with_two_candidates_stays_unresolved_and_visible():
    resolved = flatfile.resolve_models(
        {"ROGUE SPORT": 30, "ROGUE HYBRID": 10, "REDUNDANT ROGUE": 4}
    )
    assert resolved["REDUNDANT ROGUE"] == "REDUNDANT ROGUE"


def test_a_junk_name_with_a_too_short_key_stays_unresolved():
    resolved = flatfile.resolve_models(
        {"Z4": 9, "REDUNDANT Z": 2, "86 SPORT": 4, "REDUNDANT 86": 3}
    )
    assert resolved["REDUNDANT Z"] == "REDUNDANT Z", "one-character key is never extended"
    assert resolved["REDUNDANT 86"] == "REDUNDANT 86", "two-character key is never extended"
