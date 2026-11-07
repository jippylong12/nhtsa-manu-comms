"""The flat-file index: normalization, CSV hygiene, and ETag-driven ingest.

The picker and sync both read `comm_products`, and both are only ever as correct
as this ingest. The unit cases pin the normalization vocabulary and the row
hygiene JIP-428 specified (comma-packed years, 9999, junk `Model == Make` rows,
hyphen/space variants); the DB cases pin the two behaviours the daily refresh
depends on -- a window is re-ingested only when its ETag changed, and re-ingest
replaces the window's rows rather than duplicating them.

The HTTP layer is monkeypatched so the ingest path runs end to end without
downloading NHTSA's multi-megabyte zips; a real download is exercised once,
out of band, by the M3 smoke run.
"""

import pytest

from src.communications import flatfile

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
# Ingest + ETag idempotency (against the real test database)
# --------------------------------------------------------------------------

_WIN = "TESTWIN"

_CSV = [
    '"TSB/Document ID","Make","Model","Model Year","Concise Summary"',
    '"700001","HONDA","CIVIC","2020,2021","summary a"',
    '"700002","HONDA","CIVIC","9999","summary b"',
]


async def _seeded_products(conn):
    return await conn.fetch(
        "SELECT nhtsa_id, make, model, model_year, source_window "
        "FROM comm_products WHERE source_window = $1 ORDER BY nhtsa_id, model_year",
        _WIN,
    )


@pytest.mark.asyncio
async def test_ingest_window_writes_rows_and_records_the_etag(conn):
    rows = await flatfile.ingest_window(
        conn,
        _WIN,
        flatfile.parse_rows(_CSV),
        etag="E1",
        last_modified=None,
    )
    # 700001 -> (2020, 2021), 700002 -> (9999): three product rows.
    assert rows == 3

    products = await _seeded_products(conn)
    assert [(r["nhtsa_id"], r["model"], r["model_year"]) for r in products] == [
        ("700001", "CIVIC", 2020),
        ("700001", "CIVIC", 2021),
        ("700002", "CIVIC", 9999),
    ]
    recorded = await conn.fetchrow(
        "SELECT etag, row_count FROM flatfile_ingests WHERE source_window = $1", _WIN
    )
    assert recorded["etag"] == "E1"
    assert recorded["row_count"] == 3


@pytest.mark.asyncio
async def test_refresh_skips_a_window_whose_etag_is_unchanged(conn, monkeypatch):
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) VALUES ($1, 'E1', 3)",
        _WIN,
    )
    downloads = {"n": 0}

    async def fake_head(_client, _window):
        return "E1", None

    async def fake_download(_client, _window, _dest):
        downloads["n"] += 1
        return "E1", None

    monkeypatch.setattr(flatfile, "head_window", fake_head)
    monkeypatch.setattr(flatfile, "_download_window", fake_download)

    report = await flatfile.refresh_index(conn, client=object(), windows=[_WIN], force=False)

    assert report[_WIN]["status"] == "unchanged"
    assert downloads["n"] == 0, "an unchanged ETag must not trigger a download"
    assert (
        await conn.fetchval("SELECT count(*) FROM comm_products WHERE source_window=$1", _WIN) == 0
    )


@pytest.mark.asyncio
async def test_refresh_reingests_a_window_whose_etag_changed(conn, monkeypatch):
    await conn.execute(
        "INSERT INTO flatfile_ingests (source_window, etag, row_count) VALUES ($1, 'E1', 0)",
        _WIN,
    )

    async def fake_head(_client, _window):
        return "E2", None

    async def fake_download(_client, _window, _dest):
        return "E2", None

    monkeypatch.setattr(flatfile, "head_window", fake_head)
    monkeypatch.setattr(flatfile, "_download_window", fake_download)
    monkeypatch.setattr(flatfile, "_iter_csv_lines", lambda _path: iter(_CSV))

    report = await flatfile.refresh_index(conn, client=object(), windows=[_WIN], force=False)

    assert report[_WIN]["status"] == "ingested"
    assert report[_WIN]["rows"] == 3
    products = await _seeded_products(conn)
    assert {r["nhtsa_id"] for r in products} == {"700001", "700002"}
    assert (
        await conn.fetchval("SELECT etag FROM flatfile_ingests WHERE source_window=$1", _WIN)
        == "E2"
    )


@pytest.mark.asyncio
async def test_reingest_replaces_rather_than_duplicates(conn, monkeypatch):
    """A window ingested twice must leave exactly one copy of each product row --
    the delete-by-window before the merge is what guarantees it."""

    async def fake_head(_client, _window):
        return "E2", None

    async def fake_download(_client, _window, _dest):
        return "E2", None

    monkeypatch.setattr(flatfile, "head_window", fake_head)
    monkeypatch.setattr(flatfile, "_download_window", fake_download)
    monkeypatch.setattr(flatfile, "_iter_csv_lines", lambda _path: iter(_CSV))

    await flatfile.refresh_index(conn, client=object(), windows=[_WIN], force=True)
    await flatfile.refresh_index(conn, client=object(), windows=[_WIN], force=True)

    assert (
        await conn.fetchval("SELECT count(*) FROM comm_products WHERE source_window=$1", _WIN) == 3
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
