"""Sync learns a vehicle's communications from the flat-file index (ADR 0001, M3).

The discovery step no longer reads a live vehicle-details payload; it reads the
vehicle's `vehicle_bulletins` (filled by the flat-file scan, alias folding already
applied) and fetches each new bulletin's detail from `/safetyIssues/byNhtsaId`.
These tests pin that seam: association is exactly what the vehicle's scan listed,
so a bulletin filed under a different nameplate is not pulled in; already-stored
bulletins are linked without refetching; and a per-bulletin fetch failure is
counted, not fatal.

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
    """A tracked F 150 with three scanned bulletins, plus a tracked RANGER whose
    bulletin must not be associated to the F 150."""
    vehicle_id = await conn.fetchval(
        """
        INSERT INTO vehicles (year, make, model, keywords)
        VALUES (2099, 'FORD', 'F 150', ARRAY['brake']) RETURNING id
        """
    )
    ranger_id = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2099, 'FORD', 'RANGER') RETURNING id"
    )
    await conn.executemany(
        "INSERT INTO vehicle_bulletins (vehicle_id, nhtsa_id, summary, source_window) "
        "VALUES ($1, $2, $3, $4)",
        [
            (vehicle_id, "900001", "concise a", _WIN),
            (vehicle_id, "900002", "concise b", _WIN),
            (vehicle_id, "900003", "concise c", _WIN),
            (ranger_id, "900009", "wrong nameplate", _WIN),  # never associated to the F 150
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
    """A vehicle still keyed to a crash-test body style (pre-M4) is 'F 150 REGULAR
    CAB', which no flat-file row uses, so its scan lists nothing and sync finds
    nothing -- the expected state until the M4 backfill re-keys it to the plain
    nameplate."""
    await _seed(conn)
    granular_id = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2099, 'FORD', 'F 150 REGULAR CAB') "
        "RETURNING id"
    )
    vehicle = {
        "id": granular_id,
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
        "INSERT INTO vehicle_bulletins (vehicle_id, nhtsa_id, summary, source_window) "
        "VALUES ($1, '900101', 'concise', $2)",
        vehicle_id,
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


# --- which links are news for the alert email (migration 0021) --------------------------


async def _flags(conn, vehicle_id) -> dict[str, bool]:
    return {
        r["nhtsa_id"]: r["alert_pending"]
        for r in await conn.fetch(
            """
            SELECT c.nhtsa_id, cv.alert_pending FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE cv.vehicle_id = $1
            """,
            vehicle_id,
        )
    }


async def _scanned(conn, vehicle_id) -> None:
    await conn.execute(
        "INSERT INTO vehicle_scans (vehicle_id, source_window, etag) VALUES ($1, $2, 'e1')",
        vehicle_id,
        _WIN,
    )


async def _first_synced(conn, vehicle_id):
    return await conn.fetchval("SELECT first_synced_at FROM vehicles WHERE id = $1", vehicle_id)


@pytest.mark.asyncio
async def test_a_cars_first_sync_is_history_and_a_bulletin_that_arrives_later_is_news(
    conn, no_pacing
):
    vehicle = await _seed(conn)
    await _scanned(conn, vehicle["id"])
    client = _FakeClient({900001: _detail("900001"), 900002: _detail("900002")})

    await sync_job.sync_vehicle(conn, client, vehicle)

    assert await _flags(conn, vehicle["id"]) == {"900001": False, "900002": False, "900003": False}
    assert await _first_synced(conn, vehicle["id"]) is not None

    # NHTSA publishes a fourth bulletin; the next night's scan lists it.
    await conn.execute(
        "INSERT INTO vehicle_bulletins (vehicle_id, nhtsa_id, summary, source_window) "
        "VALUES ($1, '900004', 'concise d', $2)",
        vehicle["id"],
        _WIN,
    )
    await sync_job.sync_vehicle(conn, _FakeClient({900004: _detail("900004")}), vehicle)

    flags = await _flags(conn, vehicle["id"])
    assert flags["900004"] is True
    assert [nid for nid, news in flags.items() if news] == ["900004"]


@pytest.mark.asyncio
async def test_a_first_sync_with_a_failed_fetch_is_not_finished_so_the_rest_is_still_history(
    conn, no_pacing
):
    vehicle = await _seed(conn)
    await _scanned(conn, vehicle["id"])

    await sync_job.sync_vehicle(conn, _FakeClient({900001: _detail("900001"), 900002: None}), vehicle)
    assert await _first_synced(conn, vehicle["id"]) is None

    await sync_job.sync_vehicle(conn, _FakeClient({900002: _detail("900002")}), vehicle)
    assert (await _flags(conn, vehicle["id"]))["900002"] is False
    assert await _first_synced(conn, vehicle["id"]) is not None


@pytest.mark.asyncio
async def test_a_car_not_scanned_yet_or_synced_with_a_cap_is_not_marked_as_synced(conn, no_pacing):
    vehicle = await _seed(conn)
    full = {900001: _detail("900001"), 900002: _detail("900002")}

    await sync_job.sync_vehicle(conn, _FakeClient(full), vehicle)  # no vehicle_scans row
    assert await _first_synced(conn, vehicle["id"]) is None

    await _scanned(conn, vehicle["id"])
    await sync_job.sync_vehicle(conn, _FakeClient(full), vehicle, limit=1)
    assert await _first_synced(conn, vehicle["id"]) is None


@pytest.mark.asyncio
async def test_a_failed_recheck_of_a_summary_only_bulletin_does_not_hold_the_first_sync_back(
    conn, no_pacing
):
    vehicle = await _seed(conn)
    await _scanned(conn, vehicle["id"])
    # 900003 is stored already, without a document: sync asks NHTSA for it again every run.
    await conn.execute(
        "UPDATE communications SET status_reason = $1 WHERE nhtsa_id = '900003'",
        sync_job.SUMMARY_ONLY_REASON,
    )
    client = _FakeClient({900001: _detail("900001"), 900002: _detail("900002"), 900003: None})

    stats = await sync_job.sync_vehicle(conn, client, vehicle)

    assert stats.fetch_failures == 1 and stats.rechecked == 1
    assert await _first_synced(conn, vehicle["id"]) is not None


@pytest.mark.asyncio
async def test_a_bulletin_nhtsa_never_serves_stops_holding_the_first_sync_back_after_days(
    conn, no_pacing
):
    vehicle = await _seed(conn)
    await _scanned(conn, vehicle["id"])
    served = {900001: _detail("900001"), 900002: None}  # 900002 is listed but never served

    await sync_job.sync_vehicle(conn, _FakeClient(served), vehicle)
    assert await _first_synced(conn, vehicle["id"]) is None

    await conn.execute(
        "UPDATE communication_vehicles SET first_seen_at = now() - interval '4 days' "
        "WHERE vehicle_id = $1",
        vehicle["id"],
    )
    await sync_job.sync_vehicle(conn, _FakeClient(served), vehicle)
    assert await _first_synced(conn, vehicle["id"]) is not None
