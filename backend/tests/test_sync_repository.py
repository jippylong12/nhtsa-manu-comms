"""Idempotency and dedup rules for the sync job's upserts."""

from datetime import datetime, timezone

import pytest

from src.jobs import repository as repo

pytestmark = pytest.mark.asyncio


async def _insert(conn, nhtsa_id="90000001", summary="Brake shudder at highway speed"):
    return await repo.upsert_communication(
        conn,
        nhtsa_id=nhtsa_id,
        communication_number="24-NA-001",
        communication_type="TSB",
        communication_date=datetime(2024, 7, 1, tzinfo=timezone.utc),
        summary=summary,
        details_summary="details",
        raw='{"nhtsaIdNumber": "90000001"}',
    )


async def test_first_insert_reports_inserted(conn):
    comm_id, inserted = await _insert(conn)
    assert inserted is True
    assert comm_id > 0


async def test_reinsert_is_update_not_insert(conn):
    first_id, first_inserted = await _insert(conn)
    second_id, second_inserted = await _insert(conn)

    assert first_inserted is True
    assert second_inserted is False, "second upsert must report an update, not an insert"
    assert first_id == second_id, "the same nhtsa_id must not create a second row"

    count = await conn.fetchval("SELECT count(*) FROM communications WHERE nhtsa_id = '90000001'")
    assert count == 1


async def test_resync_refreshes_metadata(conn):
    await _insert(conn, summary="original summary")
    await _insert(conn, summary="corrected summary")

    summary = await conn.fetchval("SELECT summary FROM communications WHERE nhtsa_id = '90000001'")
    assert summary == "corrected summary"


async def test_resync_never_resets_a_processed_row(conn):
    """The rule the processor's cost depends on."""
    comm_id, _ = await _insert(conn)
    await conn.execute(
        """
        UPDATE communications
        SET status = 'processed', processed_at = now(), attempts = 2
        WHERE id = $1
        """,
        comm_id,
    )

    await _insert(conn, summary="metadata changed upstream")

    row = await conn.fetchrow(
        "SELECT status, attempts, processed_at FROM communications WHERE id = $1", comm_id
    )
    assert row["status"] == "processed", "re-sync must not drag a processed row back to pending"
    assert row["attempts"] == 2
    assert row["processed_at"] is not None


async def test_failed_row_also_survives_resync(conn):
    comm_id, _ = await _insert(conn)
    await conn.execute(
        "UPDATE communications SET status='failed', status_reason='pdf 404' WHERE id=$1", comm_id
    )
    await _insert(conn)

    row = await conn.fetchrow(
        "SELECT status, status_reason FROM communications WHERE id=$1", comm_id
    )
    assert row["status"] == "failed"
    assert row["status_reason"] == "pdf 404"


async def test_failure_write_cannot_clobber_a_processed_row(conn):
    """The guard added to _record_failure / _mark_failed: a late failure for a
    sibling document must not drag an already-processed communication back."""
    comm_id, _ = await _insert(conn)
    await conn.execute(
        "UPDATE communications SET status='processed', processed_at=now() WHERE id=$1", comm_id
    )

    # This is the exact guarded UPDATE the failure writers now run.
    await conn.execute(
        """
        UPDATE communications
        SET status='failed', status_reason='late sibling failure', attempts=attempts+1
        WHERE id=$1 AND status <> 'processed'
        """,
        comm_id,
    )

    row = await conn.fetchrow(
        "SELECT status, status_reason, processed_at FROM communications WHERE id=$1", comm_id
    )
    assert row["status"] == "processed", "a failure write must not clobber a processed row"
    assert row["status_reason"] is None
    assert row["processed_at"] is not None


async def test_shared_comm_is_one_row_and_two_links(conn, vehicle):
    """The core reason the schema was redesigned."""
    second = await conn.fetchrow(
        """
        INSERT INTO vehicles (year, make, model)
        VALUES (2024, 'CHEVROLET', 'OTHER TRUCK') RETURNING id
        """
    )

    comm_id, _ = await _insert(conn)
    linked_a = await repo.link_vehicle(
        conn, communication_id=comm_id, vehicle_id=vehicle["id"], matched_keywords=["battery"]
    )
    linked_b = await repo.link_vehicle(
        conn, communication_id=comm_id, vehicle_id=second["id"], matched_keywords=[]
    )

    assert linked_a is True and linked_b is True

    comms = await conn.fetchval("SELECT count(*) FROM communications WHERE nhtsa_id = '90000001'")
    links = await conn.fetchval(
        "SELECT count(*) FROM communication_vehicles WHERE communication_id = $1", comm_id
    )
    assert comms == 1, "one communication row"
    assert links == 2, "two vehicle links"


async def test_relinking_same_vehicle_is_noop(conn, vehicle):
    comm_id, _ = await _insert(conn)
    assert (
        await repo.link_vehicle(
            conn, communication_id=comm_id, vehicle_id=vehicle["id"], matched_keywords=[]
        )
        is True
    )
    assert (
        await repo.link_vehicle(
            conn, communication_id=comm_id, vehicle_id=vehicle["id"], matched_keywords=[]
        )
        is False
    )

    links = await conn.fetchval(
        "SELECT count(*) FROM communication_vehicles WHERE communication_id = $1", comm_id
    )
    assert links == 1


async def test_document_insert_is_idempotent_and_preserves_processing(conn):
    comm_id, _ = await _insert(conn)
    url = "https://static.nhtsa.gov/odi/tsbs/2024/TEST-0001.pdf"

    assert (
        await repo.upsert_document(
            conn, communication_id=comm_id, url=url, doc_summary="Bulletin", load_date=None
        )
        is True
    )

    # Simulate Job 2 having already processed this document.
    await conn.execute(
        """
        UPDATE comm_documents
        SET extracted_text='real text', llm_summary='a summary',
            symptoms=ARRAY['shudder'], extraction_method='pymupdf'
        WHERE url=$1
        """,
        url,
    )

    assert (
        await repo.upsert_document(
            conn, communication_id=comm_id, url=url, doc_summary="Bulletin", load_date=None
        )
        is False
    ), "seeing the same URL again must not report a new document"

    row = await conn.fetchrow(
        "SELECT extracted_text, llm_summary, symptoms FROM comm_documents WHERE url=$1", url
    )
    assert row["extracted_text"] == "real text", "re-sync must not wipe extracted text"
    assert row["llm_summary"] == "a summary"
    assert row["symptoms"] == ["shudder"]


async def test_existing_nhtsa_ids_filters_correctly(conn):
    await _insert(conn, nhtsa_id="90000001")
    await _insert(conn, nhtsa_id="90000002")

    found = await repo.existing_nhtsa_ids(conn, ["90000001", "90000002", "90000003"])
    assert found == {"90000001", "90000002"}
    assert await repo.existing_nhtsa_ids(conn, []) == set()


async def test_search_vector_populates_from_llm_arrays(conn):
    """Generated tsvector must index array contents, not just narrative text."""
    comm_id, _ = await _insert(conn)
    await repo.upsert_document(
        conn,
        communication_id=comm_id,
        url="https://static.nhtsa.gov/odi/tsbs/2024/TEST-0002.pdf",
        doc_summary="Bulletin",
        load_date=None,
    )
    await conn.execute(
        """
        UPDATE comm_documents
        SET llm_summary='Reprogram the module', symptoms=ARRAY['intermittent no start']
        WHERE communication_id=$1
        """,
        comm_id,
    )

    hits = await conn.fetchval(
        """
        SELECT count(*) FROM comm_documents
        WHERE communication_id=$1 AND search_tsv @@ plainto_tsquery('english', 'no start')
        """,
        comm_id,
    )
    assert hits == 1


async def test_vehicle_upsert_is_idempotent(conn):
    first_id, inserted = await repo.upsert_vehicle(
        conn, year=2025, make="GMC", model="SIERRA EV", keywords=["battery"]
    )
    second_id, reinserted = await repo.upsert_vehicle(
        conn, year=2025, make="GMC", model="SIERRA EV"
    )
    assert inserted is True and reinserted is False
    assert first_id == second_id

    keywords = await conn.fetchval("SELECT keywords FROM vehicles WHERE id = $1", first_id)
    assert list(keywords) == ["battery"], "an add with no keywords keeps the configured ones"


async def _upsert_as(conn, communication_type, nhtsa_id="90000001"):
    return await repo.upsert_communication(
        conn,
        nhtsa_id=nhtsa_id,
        communication_number="24-NA-001",
        communication_type=communication_type,
        communication_date=datetime(2024, 7, 1, tzinfo=timezone.utc),
        summary="s",
        details_summary="d",
        raw="{}",
    )


async def _severity(conn, nhtsa_id="90000001"):
    row = await conn.fetchrow(
        "SELECT severity, severity_source FROM communications WHERE nhtsa_id = $1", nhtsa_id
    )
    return row["severity"], row["severity_source"]


async def test_new_tsb_is_serious_by_type(conn):
    await _upsert_as(conn, "TSB")
    assert await _severity(conn) == ("serious", "type")


async def test_type_change_refreshes_severity_while_source_is_type(conn):
    await _upsert_as(conn, "TSB")
    await _upsert_as(conn, "IB")
    assert await _severity(conn) == ("small", "type")


async def test_classifier_severity_survives_resync(conn):
    comm_id, _ = await _upsert_as(conn, "MC")
    await conn.execute(
        "UPDATE communications SET severity = 'serious', severity_source = 'classifier' "
        "WHERE id = $1",
        comm_id,
    )
    await _upsert_as(conn, "IB")
    assert await _severity(conn) == ("serious", "classifier")


async def test_migration_0010_applied_twice_is_a_noop(conn):
    from pathlib import Path

    sql = (Path(__file__).parent.parent / "migrations" / "0010_schema_v2.sql").read_text()
    comm_id, _ = await _upsert_as(conn, "TSB")
    await conn.execute(
        "UPDATE communications SET severity = 'medium', severity_source = 'classifier' "
        "WHERE id = $1",
        comm_id,
    )
    await _upsert_as(conn, "PIT", nhtsa_id="90000002")
    before = await conn.fetch("SELECT id, severity, severity_source FROM communications ORDER BY id")
    await conn.execute(sql)
    await conn.execute(sql)
    after = await conn.fetch("SELECT id, severity, severity_source FROM communications ORDER BY id")
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert await _severity(conn, "90000002") == ("medium", "type")


async def test_migration_0011_moves_old_documentless_failures_and_is_idempotent(conn):
    from pathlib import Path

    sql = (
        Path(__file__).parent.parent / "migrations" / "0011_summary_only_backfill.sql"
    ).read_text()
    old, _ = await _insert(conn, nhtsa_id="90000011")
    other, _ = await _insert(conn, nhtsa_id="90000012")
    await conn.execute(
        "UPDATE communications SET status = 'failed', "
        "status_reason = 'no associated documents published by NHTSA' WHERE id = $1",
        old,
    )
    await conn.execute(
        "UPDATE communications SET status = 'failed', status_reason = 'document x: boom' "
        "WHERE id = $1",
        other,
    )

    async def snap():
        rows = await conn.fetch(
            "SELECT id, status, status_reason, processed_at FROM communications ORDER BY id"
        )
        return [tuple(r) for r in rows]

    await conn.execute(sql)
    first = await snap()
    await conn.execute(sql)
    assert await snap() == first

    by_id = {r[0]: r for r in first}
    assert by_id[old][1:3] == ("processed", "summary only: NHTSA published no document")
    assert by_id[old][3] is not None
    assert by_id[other][1:3] == ("failed", "document x: boom")
