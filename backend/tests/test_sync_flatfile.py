"""Sync learns a vehicle's communications from the flat-file index (ADR 0001, M3).

The discovery step no longer reads a live vehicle-details payload; it queries
`comm_products` for the vehicle's plain nameplate and fetches each new bulletin's
detail from `/safetyIssues/byNhtsaId`. These tests pin that seam: association is an
exact match on `(year, make, normalized model)` so a bulletin filed under a
different nameplate is not pulled in; already-stored bulletins are linked without
refetching; and a per-bulletin fetch failure is counted, not fatal.

Everything runs against the real database inside a rolled-back transaction, with a
fake client standing in for NHTSA -- the same seam sync uses in production.
"""

import pytest

from src.jobs import sync as sync_job

_VID = 999_123_456
_WIN = "TESTSYNC"


class _FakeClient:
    """Stands in for NHTSAClient: yields canned byNhtsaId details, or None."""

    def __init__(self, comms: dict[int, dict | None]):
        self._comms = comms
        self.requested: list[int] = []

    async def fetch_communications_batch(self, nhtsa_ids, max_concurrent: int = 3):
        for nid in nhtsa_ids:
            self.requested.append(nid)
            yield nid, self._comms.get(nid)


def _detail(nhtsa_id: str) -> dict:
    return {
        "nhtsaIdNumber": nhtsa_id,
        "summary": f"full byNhtsaId summary for {nhtsa_id} mentioning brake wear",
        "manufacturerCommunicationNumber": f"SB-{nhtsa_id}",
        "communicationDate": "2099-03-01",
        "associatedDocuments": [
            {
                "url": f"https://example.test/{nhtsa_id}.pdf",
                "summary": "doc",
                "loadDate": "2099-03-02",
            }
        ],
    }


@pytest.fixture
def no_pacing(monkeypatch):
    """The 0.2s per-request pacing is politeness to NHTSA, not part of the test."""
    import asyncio

    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


async def _seed(conn):
    """A tracked F 150 plus an index slice: three of its bulletins and one
    RANGER bulletin that must not be associated to it."""
    vehicle_id = await conn.fetchval(
        """
        INSERT INTO vehicles (year, make, model, keywords)
        VALUES (2099, 'FORD', 'F 150', ARRAY['brake']) RETURNING id
        """
    )
    await conn.executemany(
        "INSERT INTO comm_products (nhtsa_id, make, model, model_year, summary, source_window) "
        "VALUES ($1, 'FORD', $2, 2099, $3, $4)",
        [
            ("900001", "F 150", "concise a", _WIN),
            ("900002", "F 150", "concise b", _WIN),
            ("900003", "F 150", "concise c", _WIN),
            ("900009", "RANGER", "wrong nameplate", _WIN),  # must never be associated to the F 150
        ],
    )
    # 900003 already exists in the corpus: sync should link it, not refetch it.
    await conn.execute("INSERT INTO communications (nhtsa_id, status) VALUES ('900003', 'pending')")
    return {
        "id": vehicle_id,
        "year": 2099,
        "make": "FORD",
        "model": "F 150",
        "keywords": ["brake"],
    }


@pytest.mark.asyncio
async def test_sync_associates_via_the_index_and_records_partial_failure(conn, no_pacing):
    vehicle = await _seed(conn)
    client = _FakeClient({900001: _detail("900001"), 900002: None})  # 900002 fails to fetch

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    # Found exactly the three F 150 bulletins -- the RANGER row is a different
    # nameplate and never enters the picture.
    assert stats.found == 3
    assert 900009 not in client.requested, "a different nameplate must not be fetched"
    assert stats.skipped_known == 1, "the already-stored 900003 is linked, not refetched"
    assert 900003 not in client.requested
    assert stats.new_comms == 1, "only 900001 was newly stored"
    assert stats.fetch_failures == 1, "900002 failed to fetch and is counted, not fatal"
    assert not stats.errors

    # 900001 and the pre-existing 900003 are linked to the vehicle; 900002 (failed)
    # and 900009 (wrong nameplate) are not.
    linked = {
        r["nhtsa_id"]
        for r in await conn.fetch(
            """
            SELECT c.nhtsa_id FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE cv.vehicle_id = $1
            """,
            vehicle["id"],
        )
    }
    assert linked == {"900001", "900003"}


@pytest.mark.asyncio
async def test_stored_bulletin_carries_the_index_summary_and_byNhtsaId_fields(conn, no_pacing):
    vehicle = await _seed(conn)
    client = _FakeClient({900001: _detail("900001"), 900002: _detail("900002")})

    await sync_job.sync_vehicle(conn, client, vehicle)

    row = await conn.fetchrow(
        "SELECT summary, details_summary, communication_number FROM communications "
        "WHERE nhtsa_id = '900001'"
    )
    # summary is the full byNhtsaId text; details_summary is the concise summary
    # that used to come from the vehicle-details payload and now comes from the index.
    assert row["summary"] == "full byNhtsaId summary for 900001 mentioning brake wear"
    assert row["details_summary"] == "concise a"
    assert row["communication_number"] == "SB-900001"

    doc = await conn.fetchval(
        "SELECT count(*) FROM comm_documents d JOIN communications c ON c.id = d.communication_id "
        "WHERE c.nhtsa_id = '900001'"
    )
    assert doc == 1


@pytest.mark.asyncio
async def test_a_granular_bodystyle_nameplate_matches_nothing_until_migrated(conn, no_pacing):
    """A vehicle still keyed to a crash-test body style (pre-M4) normalizes to
    'F 150 REGULAR CAB', which no index row uses, so sync finds nothing -- the
    expected state until the M4 backfill re-keys it to the plain nameplate."""
    seeded = await _seed(conn)
    vehicle = {
        "id": seeded["id"],
        "year": 2099,
        "make": "FORD",
        "model": "F-150 REGULAR CAB",
        "keywords": [],
    }
    client = _FakeClient({})

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert stats.found == 0
    assert client.requested == []


@pytest.mark.asyncio
async def test_when_every_fetch_fails_all_are_counted_not_raised_and_it_returns_promptly(
    conn, no_pacing
):
    """D3 (JIP-435): NHTSA byNhtsaId down for everything degrades to counts."""
    import time

    vehicle = await _seed(conn)
    await conn.execute("DELETE FROM communications WHERE nhtsa_id = '900003'")
    client = _FakeClient({})  # every byNhtsaId returns None

    started = time.monotonic()
    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert time.monotonic() - started < 1.0
    assert stats.found == 3
    assert stats.fetch_failures == stats.found
    assert stats.errors == []


_SUMMARY_ONLY = "summary only: NHTSA published no document"


async def _seed_summary_only(conn):
    """A tracked F 150 with one stored summary-only bulletin (no document row)."""
    vehicle_id = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model, keywords) "
        "VALUES (2099, 'FORD', 'F 150', ARRAY['brake']) RETURNING id"
    )
    await conn.execute(
        "INSERT INTO comm_products (nhtsa_id, make, model, model_year, summary, source_window) "
        "VALUES ('900101', 'FORD', 'F 150', 2099, 'concise', $1)",
        _WIN,
    )
    comm_id = await conn.fetchval(
        "INSERT INTO communications (nhtsa_id, status, status_reason, processed_at) "
        "VALUES ('900101', 'processed', $1, now()) RETURNING id",
        _SUMMARY_ONLY,
    )
    vehicle = {
        "id": vehicle_id,
        "year": 2099,
        "make": "FORD",
        "model": "F 150",
        "keywords": ["brake"],
    }
    return vehicle, comm_id


async def _state(conn, comm_id):
    row = await conn.fetchrow(
        "SELECT status, status_reason, processed_at FROM communications WHERE id = $1", comm_id
    )
    docs = await conn.fetchval(
        "SELECT count(*) FROM comm_documents WHERE communication_id = $1", comm_id
    )
    return row, docs


@pytest.mark.asyncio
async def test_a_late_document_reopens_a_summary_only_communication(conn, no_pacing):
    vehicle, comm_id = await _seed_summary_only(conn)
    client = _FakeClient({900101: _detail("900101")})

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert client.requested == [900101], "the summary-only bulletin is asked for again"
    assert (stats.rechecked, stats.reopened) == (1, 1)
    row, docs = await _state(conn, comm_id)
    assert row["status"] == "pending"
    assert row["status_reason"] is None
    assert row["processed_at"] is None
    assert docs == 1


@pytest.mark.asyncio
async def test_a_still_documentless_bulletin_stays_summary_only(conn, no_pacing):
    vehicle, comm_id = await _seed_summary_only(conn)
    detail = _detail("900101")
    detail["associatedDocuments"] = []
    client = _FakeClient({900101: detail})

    stats = await sync_job.sync_vehicle(conn, client, vehicle)
    await sync_job.sync_vehicle(conn, client, vehicle)

    assert client.requested == [900101, 900101]
    assert (stats.rechecked, stats.reopened) == (1, 0)
    row, docs = await _state(conn, comm_id)
    assert (row["status"], row["status_reason"]) == ("processed", _SUMMARY_ONLY)
    assert row["processed_at"] is not None
    assert docs == 0
