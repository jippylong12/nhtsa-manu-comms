"""Discovery is served from the flat-file index, and only from it (ADR 0001, M3).

The three picker dropdowns are now bounded Postgres queries against
`comm_vocab` -- no NHTSA call, so nothing in the interactive path can time out.
These tests seed a small, isolated slice of the vocabulary (a far-future year no
real corpus row uses, tagged with a private source_window) and assert the service
returns plain nameplates from it: years exclude the 9999 unknown sentinel, makes
are curated against TOP_MAKES, and models come back as the canonical nameplates.

Seeded through a committed connection and cleaned up after, because the service
reads through its own pool connection and would not see an uncommitted row.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.communications.service import CommunicationService
from src.config import get_settings

_TAG = "TESTDISC"
_YEAR = 2099  # newer than any real corpus row, so it sorts to the top deterministically


async def _insert_vocab(conn, rows, tag):
    """Seed comm_vocab from (nhtsa_id, make, model, model_year) rows.

    The bulletin count of a triple is its distinct nhtsa_ids, as the ingest
    computes it; the counts are what drive resolve_models' folding.
    """
    ids: dict[tuple[int, str, str], set[str]] = {}
    for nid, make, model, year in rows:
        ids.setdefault((year, make, model), set()).add(nid)
    await conn.executemany(
        "INSERT INTO comm_vocab (model_year, make, model, source_window, bulletins) "
        "VALUES ($1, $2, $3, $4, $5)",
        [(year, make, model, tag, len(nids)) for (year, make, model), nids in ids.items()],
    )


async def _wipe_vocab(conn, tag):
    await conn.execute("DELETE FROM comm_vocab WHERE source_window = $1", tag)


@pytest_asyncio.fixture
async def seeded():
    """Populate an isolated slice of comm_vocab, removed before and after."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    rows = [
        # (nhtsa_id, make, model, model_year)
        ("800001", "FORD", "F 150", _YEAR),
        ("800002", "FORD", "F 150 LIGHTNING BEV", _YEAR),
        ("800003", "FORD", "RANGER", _YEAR),
        ("800004", "HONDA", "CIVIC", _YEAR),
        ("800005", "ACMEZZ", "WIDGET", _YEAR),  # a make outside TOP_MAKES: must be curated out
        ("800006", "FORD", "BRONCO", _YEAR - 1),  # an older year, for the years query
        ("800007", "FORD", "UNKNOWN", 9999),  # the unknown-year sentinel: must be excluded
    ]

    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe_vocab(conn, _TAG)
        await _insert_vocab(conn, rows, _TAG)
    finally:
        await conn.close()

    yield

    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe_vocab(conn, _TAG)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_years_exclude_the_unknown_sentinel_and_sort_newest_first(seeded):
    years = await CommunicationService.get_discovery_years()
    assert _YEAR in years, "the seeded model year is offered"
    assert _YEAR - 1 in years
    assert 9999 not in years, "9999 (unknown) must never be offered as a year"
    assert years.index(_YEAR) < years.index(_YEAR - 1), "years are newest first"


@pytest.mark.asyncio
async def test_makes_are_curated_against_top_makes(seeded):
    makes = await CommunicationService.get_discovery_makes(_YEAR)
    assert makes == ["FORD", "HONDA"], "only TOP_MAKES with comms that year, in TOP_MAKES order"
    assert "ACMEZZ" not in makes, "a make outside the curated list is not offered"


@pytest.mark.asyncio
async def test_models_are_plain_nameplates_sorted(seeded):
    models = await CommunicationService.get_discovery_models(_YEAR, "FORD")
    assert models == ["F 150", "F 150 LIGHTNING BEV", "RANGER"]


@pytest.mark.asyncio
async def test_model_lookup_is_case_insensitive_on_make(seeded):
    """The picker sends whatever case the make came in as; the index is uppercase."""
    assert await CommunicationService.get_discovery_models(_YEAR, "ford") == [
        "F 150",
        "F 150 LIGHTNING BEV",
        "RANGER",
    ]


@pytest.mark.asyncio
async def test_a_make_with_no_comms_that_year_returns_no_models(seeded):
    assert await CommunicationService.get_discovery_models(_YEAR, "TESLA") == []


# --------------------------------------------------------------------------
# Junk and duplicate names (JIP re-key follow-up): the picker lists each real
# nameplate once. Names that fold away are not lost -- sync, preview and
# identity_check match every spelling that resolves to the vehicle's model (see
# flatfile.resolve_models and tests/test_alias_matching.py), so no bulletin
# becomes unreachable.
# --------------------------------------------------------------------------

_DIRTY_TAG = "TESTDISC_DIRTY"
_DIRTY_YEAR = 2098


async def _seed_dirty(rows: list[tuple[str, str, str]]):
    """Insert (nhtsa_id, make, model) rows for _DIRTY_YEAR; return a cleanup coroutine fn."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    async def wipe():
        conn = await asyncpg.connect(settings.database_url)
        try:
            await _wipe_vocab(conn, _DIRTY_TAG)
        finally:
            await conn.close()

    await wipe()
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _insert_vocab(
            conn, [(nid, mk, md, _DIRTY_YEAR) for nid, mk, md in rows], _DIRTY_TAG
        )
    finally:
        await conn.close()
    return wipe


@pytest_asyncio.fixture
async def dirty_ford():
    wipe = await _seed_dirty(
        [
            # Real nameplates, untouched.
            ("900001", "FORD", "F 150"),
            ("900002", "FORD", "RANGER"),
            # REDUNDANT twin whose every bulletin is also under the real nameplate.
            ("900003", "FORD", "ESCAPE"),
            ("900004", "FORD", "ESCAPE"),
            ("900003", "FORD", "REDUNDANT ESCAPE"),
        ]
    )
    yield
    await wipe()


@pytest.mark.asyncio
async def test_a_redundant_twin_fully_covered_by_its_real_nameplate_is_hidden(dirty_ford):
    models = await CommunicationService.get_discovery_models(_DIRTY_YEAR, "FORD")
    assert models == ["ESCAPE", "F 150", "RANGER"]


@pytest_asyncio.fixture
async def aliased_junk_ford():
    wipe = await _seed_dirty(
        [
            # REDUNDANT MACH E has no same-key sibling, but exactly one real name
            # ends with its key, so it folds into MUSTANG MACH E.
            ("900101", "FORD", "MUSTANG MACH E"),
            ("900102", "FORD", "REDUNDANT MACH E"),
            # A same-key sibling that lacks one of the junk bulletins still absorbs it:
            # matching follows the alias, so 900104 stays reachable.
            ("900103", "FORD", "BRONCO"),
            ("900103", "FORD", "REDUNDANT BRONCO"),
            ("900104", "FORD", "REDUNDANT BRONCO"),
            # DUPLICATE is treated like REDUNDANT.
            ("900105", "FORD", "EDGE"),
            ("900105", "FORD", "DUPLICATE EDGE"),
            # No real name resembles this one, so it is never guessed at.
            ("900106", "FORD", "REDUNDANT WIDGETZ"),
        ]
    )
    yield
    await wipe()


@pytest.mark.asyncio
async def test_junk_names_that_resolve_to_a_real_nameplate_disappear_the_rest_stay(
    aliased_junk_ford,
):
    models = await CommunicationService.get_discovery_models(_DIRTY_YEAR, "FORD")
    assert models == ["BRONCO", "EDGE", "MUSTANG MACH E", "REDUNDANT WIDGETZ"]


@pytest_asyncio.fixture
async def spaced_ford():
    wipe = await _seed_dirty(
        [
            # F53 is a subset of F 53, and F 53 carries more bulletins: one entry, "F 53".
            ("900201", "FORD", "F 53"),
            ("900202", "FORD", "F 53"),
            ("900203", "FORD", "F 53"),
            ("900201", "FORD", "F53"),
            # The unspaced spelling carries more bulletins, so it is the one listed.
            ("900211", "FORD", "E350"),
            ("900212", "FORD", "E350"),
            ("900211", "FORD", "E 350"),
            # Tie on bulletins and the same set: the spaced spelling wins.
            ("900221", "FORD", "GT 40"),
            ("900221", "FORD", "GT40"),
            # Not a collision: different nameplates.
            ("900231", "FORD", "F 59"),
        ]
    )
    yield
    await wipe()


@pytest.mark.asyncio
async def test_spelling_variants_that_lose_nothing_fold_into_one_entry(spaced_ford):
    models = await CommunicationService.get_discovery_models(_DIRTY_YEAR, "FORD")
    assert models == ["E350", "F 53", "F 59", "GT 40"]


@pytest_asyncio.fixture
async def split_spelling_nissan():
    wipe = await _seed_dirty(
        [
            # Each spelling holds a bulletin the other lacks (like RS 5 / RS5 in the
            # real index). They still fold: matching reads both spellings.
            ("900301", "NISSAN", "NV 200"),
            ("900302", "NISSAN", "NV 200"),
            ("900302", "NISSAN", "NV200"),
            ("900303", "NISSAN", "NV200"),
            ("900303", "NISSAN", "REDUNDANT NV200"),
        ]
    )
    yield
    await wipe()


@pytest.mark.asyncio
async def test_spellings_that_each_hold_unique_bulletins_still_fold_to_one_entry(
    split_spelling_nissan,
):
    models = await CommunicationService.get_discovery_models(_DIRTY_YEAR, "NISSAN")
    assert models == ["NV 200"]
