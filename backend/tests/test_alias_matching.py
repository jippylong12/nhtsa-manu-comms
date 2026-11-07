"""Matching follows the picker's aliases, so folding a name never strands a bulletin.

The picker lists ``F 250 SD`` once even though NHTSA files some of its bulletins under
``REDUNDANT F 250`` (and ``F53`` under ``F 53``). A vehicle tracked as the listed name
must therefore count those bulletins too, in sync, in the refresh preview and in the
identity check. The scan folds the aliases (``flatfile.model_aliases``, read from the
vocabulary the same ingest just wrote) into the vehicle's ``vehicle_bulletins``, and all
three read that list, so they cannot disagree with ``/discovery/models``.

Seeded in a made-up make and year 2099 so no replica vehicle is involved.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.communications import flatfile
from src.config import get_settings
from src.jobs import sync as sync_job
from src.refresh import service as refresh_service
from src.scripts import identity_check as ic

_MAKE = "TESTALIAS"
_YEAR = 2099
_WIN = "TESTALIAS"

# nhtsa_id, model, concise summary
_ROWS = [
    ("990001", "F 250 SD", "real a"),
    ("990002", "F 250 SD", "real b"),
    ("990003", "REDUNDANT F 250", "filed only under the junk spelling"),
    ("990002", "REDUNDANT F 250", "also under the real name"),
    ("990011", "F 53", "spaced"),
    ("990012", "F 53", "spaced"),
    ("990013", "F53", "only under the unspaced spelling"),
    ("990021", "RANGER", "unrelated nameplate"),
]


class _FakeClient:
    def __init__(self):
        self.requested: list[int] = []

    async def fetch_communications_batch(self, nhtsa_ids, max_concurrent: int = 3):
        for nid in nhtsa_ids:
            self.requested.append(nid)
            yield nid, None


@pytest.fixture
def no_pacing(monkeypatch):
    import asyncio

    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


async def _seed(conn, *models: str) -> list[dict]:
    """Ingest ``_ROWS`` as window ``_WIN``, scanning one tracked vehicle per model given.

    Goes through the real ingest, so the vocabulary and the vehicles' bulletins (with
    alias folding) are exactly what a scan would produce.
    """
    vehicles = []
    for model in models:
        vid = await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
            _YEAR,
            _MAKE,
            model,
        )
        vehicles.append({"id": vid, "year": _YEAR, "make": _MAKE, "model": model, "keywords": []})
    await flatfile.ingest_window(
        conn,
        _WIN,
        [(nid, _MAKE, model, _YEAR, summary) for nid, model, summary in _ROWS],
        etag="E1",
        last_modified=None,
        vehicles=vehicles,
    )
    return vehicles


@pytest.mark.asyncio
async def test_a_listed_nameplate_owns_every_spelling_that_folds_into_it(conn):
    await _seed(conn)

    assert await flatfile.model_aliases(conn, _YEAR, _MAKE, "F 250 SD") == [
        "F 250 SD",
        "REDUNDANT F 250",
    ]
    assert await flatfile.model_aliases(conn, _YEAR, _MAKE, "F 53") == ["F 53", "F53"]


@pytest.mark.asyncio
async def test_a_vehicle_stored_under_the_folded_spelling_reaches_the_same_bulletins(conn):
    await _seed(conn)

    assert await flatfile.model_aliases(conn, _YEAR, _MAKE, "F53") == ["F 53", "F53"]


@pytest.mark.asyncio
async def test_a_model_nothing_folds_into_matches_exactly_as_before(conn):
    await _seed(conn)

    assert await flatfile.model_aliases(conn, _YEAR, _MAKE, "RANGER") == ["RANGER"]
    assert await flatfile.model_aliases(conn, _YEAR, _MAKE, "NOT INDEXED") == ["NOT INDEXED"]


@pytest.mark.asyncio
async def test_sync_finds_a_bulletin_filed_only_under_an_alias_spelling(conn, no_pacing):
    (vehicle,) = await _seed(conn, "F 250 SD")
    client = _FakeClient()

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert stats.found == 3, "990001, 990002 and the junk-only 990003"
    assert sorted(client.requested) == [990001, 990002, 990003]


@pytest.mark.asyncio
async def test_a_bulletin_filed_under_two_spellings_is_counted_once(conn, no_pacing):
    (vehicle,) = await _seed(conn, "F 250 SD")  # 990002 is under both spellings

    stats = await sync_job.sync_vehicle(conn, _FakeClient(), vehicle)

    assert stats.found == 3, "990002 is one bulletin, not two"


@pytest.mark.asyncio
async def test_sync_for_the_folded_spacing_variant_sees_both_spellings(conn, no_pacing):
    (vehicle,) = await _seed(conn, "F 53")

    stats = await sync_job.sync_vehicle(conn, _FakeClient(), vehicle)

    assert stats.found == 3, "990011, 990012 and the F53-only 990013"


@pytest.mark.asyncio
async def test_sync_for_a_vehicle_nothing_folds_into_is_unchanged(conn, no_pacing):
    (vehicle,) = await _seed(conn, "RANGER")
    client = _FakeClient()

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert stats.found == 1
    assert client.requested == [990021]


@pytest_asyncio.fixture
async def committed_vehicle():
    """A committed F 250 SD plus the index slice: preview reads through its own pool."""
    settings = get_settings()

    async def wipe(c):
        for table in ("comm_vocab", "vehicle_bulletins", "vehicle_scans", "flatfile_ingests"):
            await c.execute(f"DELETE FROM {table} WHERE source_window = $1", _WIN)
        await c.execute(
            "DELETE FROM vehicles WHERE year=$1 AND make=$2 AND model=$3",
            _YEAR,
            _MAKE,
            "F 250 SD",
        )

    c = await asyncpg.connect(settings.database_url)
    try:
        await wipe(c)
        (vehicle,) = await _seed(c, "F 250 SD")
        yield vehicle["id"]
    finally:
        await wipe(c)
        await c.close()


@pytest.mark.asyncio
async def test_preview_counts_bulletins_filed_under_an_alias_spelling(committed_vehicle):
    result = await refresh_service.preview(committed_vehicle)

    assert result["listed_by_nhtsa"] == 3, "990001, 990002 and the junk-only 990003"
    assert result["new_bulletins"] == 3


@pytest.mark.asyncio
async def test_identity_check_counts_index_bulletins_under_alias_spellings(conn):
    (vehicle,) = await _seed(conn, "F 250 SD")
    vid = vehicle["id"]

    vehicles, _orphans = await ic.collect(conn)

    row = next(r for r in vehicles if r["id"] == vid)
    assert row["index"] == 3, "990001, 990002 and the junk-only 990003, counted once each"
