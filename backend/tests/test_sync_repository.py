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


async def test_shared_comm_is_one_row_and_two_links(conn, vehicle):
    """The core reason the schema was redesigned."""
    second = await conn.fetchrow(
        """
        INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model)
        VALUES (999002, 2024, 'CHEVROLET', 'OTHER TRUCK') RETURNING id
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
        conn, nhtsa_vehicle_id=999003, year=2025, make="GMC", model="SIERRA EV"
    )
    second_id, reinserted = await repo.upsert_vehicle(
        conn, nhtsa_vehicle_id=999003, year=2025, make="GMC", model="SIERRA EV DENALI"
    )
    assert inserted is True and reinserted is False
    assert first_id == second_id

    model = await conn.fetchval("SELECT model FROM vehicles WHERE nhtsa_vehicle_id=999003")
    assert model == "SIERRA EV DENALI"
