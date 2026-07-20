"""Filters, full-text search, pagination, and detail for the corpus read API.

Seeds a small, known fixture inside the rolled-back transaction, then drives the
service functions against it. The service acquires its own pool connections, so
the fixture is committed on a dedicated connection and torn down explicitly at
the end rather than relying on the shared rollback fixture.
"""

from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.corpus import service

pytestmark = pytest.mark.asyncio

# Namespaced so a failed run cannot collide with real data or a previous run.
NS = "TESTCORPUS"


@pytest_asyncio.fixture
async def seeded():
    """Insert a known corpus fixture; remove it afterwards.

    Uses a committed connection (not the rollback fixture) because the service
    under test opens its own pool connections and would not see uncommitted
    rows.
    """
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    conn = await asyncpg.connect(settings.database_url)
    try:
        await _cleanup(conn)

        v1 = await conn.fetchval(
            "INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model) "
            "VALUES (990001, 2024, 'CHEVROLET', $1) RETURNING id",
            f"{NS} SILVERADO",
        )
        v2 = await conn.fetchval(
            "INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model) "
            "VALUES (990002, 2025, 'GMC', $1) RETURNING id",
            f"{NS} SIERRA",
        )

        # Comm A: processed, brake shudder, linked to BOTH vehicles.
        a = await conn.fetchval(
            """
            INSERT INTO communications
                (nhtsa_id, communication_number, communication_type,
                 communication_date, summary, status)
            VALUES ($1, '24-NA-A', 'TSB', '2099-06-01', 'Brake shudder bulletin', 'processed')
            RETURNING id
            """,
            f"{NS}-A",
        )
        await conn.execute(
            """
            INSERT INTO comm_documents
                (communication_id, url, extraction_method, llm_summary,
                 symptoms, systems, components, remedy, doc_kind)
            VALUES ($1, $2, 'pymupdf', 'Front brake rotors may cause a shudder.',
                    ARRAY['shudder','vibration'], ARRAY['brakes'], ARRAY['rotor'],
                    'Resurface or replace rotors.', 'service_procedure')
            """,
            a,
            f"https://static.nhtsa.gov/{NS}-A.pdf",
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1,$2),($1,$3)",
            a,
            v1,
            v2,
        )

        # Comm B: processed, infotainment, linked to v1 only, later date.
        b = await conn.fetchval(
            """
            INSERT INTO communications
                (nhtsa_id, communication_number, communication_type,
                 communication_date, summary, status)
            VALUES ($1, '24-NA-B', 'PIT', '2099-09-01', 'Infotainment update', 'processed')
            RETURNING id
            """,
            f"{NS}-B",
        )
        await conn.execute(
            """
            INSERT INTO comm_documents
                (communication_id, url, extraction_method, llm_summary,
                 symptoms, systems, components, doc_kind)
            VALUES ($1, $2, 'pymupdf', 'Radio black screen after OTA update.',
                    ARRAY['black screen'], ARRAY['infotainment'], ARRAY['radio'],
                    'service_procedure')
            """,
            b,
            f"https://static.nhtsa.gov/{NS}-B.pdf",
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1,$2)",
            b,
            v1,
        )

        # Comm C: still pending, no documents, and NULL summary (the schema
        # permits it) to guard against the coalesce regression.
        await conn.execute(
            """
            INSERT INTO communications (nhtsa_id, communication_type, communication_date, summary, status)
            VALUES ($1, 'WA', '2099-03-01', NULL, 'pending')
            """,
            f"{NS}-C",
        )

        yield {"v1": 990001, "v2": 990002}
    finally:
        await _cleanup(conn)
        await conn.close()


async def _cleanup(conn):
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS}-%")
    await conn.execute("DELETE FROM vehicles WHERE model LIKE $1", f"{NS}%")


def _ids(items):
    return {i["nhtsa_id"] for i in items}


async def test_list_returns_all_seeded(seeded):
    items, total = await service.list_communications(search=None, per_page=100)
    ours = {i["nhtsa_id"] for i in items if i["nhtsa_id"].startswith(NS)}
    assert ours == {f"{NS}-A", f"{NS}-B", f"{NS}-C"}
    assert total >= 3


async def test_filter_by_vehicle_dedupes_shared_comm(seeded):
    items, _ = await service.list_communications(vehicle_id=seeded["v2"], per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    # v2 is linked only to comm A. A appears exactly once despite two links.
    assert _ids(ours) == {f"{NS}-A"}
    assert len(ours) == 1


async def test_shared_comm_lists_both_vehicles(seeded):
    items, _ = await service.list_communications(vehicle_id=seeded["v2"], per_page=100)
    comm_a = next(i for i in items if i["nhtsa_id"] == f"{NS}-A")
    vehicle_ids = {v["nhtsa_vehicle_id"] for v in comm_a["vehicles"]}
    assert vehicle_ids == {seeded["v1"], seeded["v2"]}


async def test_filter_by_comm_type(seeded):
    items, _ = await service.list_communications(comm_type="PIT", per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-B"}


async def test_filter_by_status(seeded):
    items, _ = await service.list_communications(status="pending", per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-C"}


async def test_filter_by_system_tag(seeded):
    items, _ = await service.list_communications(systems=["brakes"], per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-A"}


async def test_filter_by_component_tag(seeded):
    items, _ = await service.list_communications(components=["radio"], per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-B"}


async def test_fulltext_matches_document_body(seeded):
    """'shudder' lives only in comm A's document text, not its NHTSA summary."""
    items, _ = await service.list_communications(search="shudder", per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-A"}


async def test_fulltext_matches_symptom_array(seeded):
    items, _ = await service.list_communications(search="black screen", per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-B"}


async def test_fulltext_no_false_positive(seeded):
    items, _ = await service.list_communications(search="transmission fluid leak", per_page=100)
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert ours == []


async def test_date_range_filter(seeded):
    # Fixtures are future-dated (2099) so they sort to the top of the populated
    # table; a cut between June and September isolates comm B.
    items, _ = await service.list_communications(
        date_from=datetime(2099, 7, 1, tzinfo=timezone.utc), per_page=100
    )
    ours = [i for i in items if i["nhtsa_id"].startswith(NS)]
    assert _ids(ours) == {f"{NS}-B"}  # only the September comm


async def test_pagination_is_consistent(seeded):
    page1, total = await service.list_communications(per_page=2, page=1)
    page2, _ = await service.list_communications(per_page=2, page=2)
    assert total >= 3
    ids1 = {i["nhtsa_id"] for i in page1}
    ids2 = {i["nhtsa_id"] for i in page2}
    assert not (ids1 & ids2), "pages must not overlap"
    assert len(page1) == 2


async def test_list_rolls_up_tags(seeded):
    items, _ = await service.list_communications(comm_type="TSB", per_page=100)
    comm_a = next(i for i in items if i["nhtsa_id"] == f"{NS}-A")
    assert set(comm_a["systems"]) == {"brakes"}
    assert "shudder" in comm_a["symptoms"]
    assert comm_a["document_count"] == 1


async def test_detail_includes_documents_and_vehicles(seeded):
    detail = await service.get_communication(f"{NS}-A")
    assert detail is not None
    assert detail["status"] == "processed"
    assert len(detail["documents"]) == 1
    doc = detail["documents"][0]
    assert doc["remedy"] == "Resurface or replace rotors."
    assert doc["has_embedding"] is False
    assert {v["nhtsa_vehicle_id"] for v in detail["vehicles"]} == {seeded["v1"], seeded["v2"]}


async def test_detail_missing_returns_none(seeded):
    assert await service.get_communication(f"{NS}-DOESNOTEXIST") is None


async def test_null_summary_coalesced_to_empty_string(seeded):
    """A NULL summary (schema-permitted) must come back as '' from both the list
    and detail, so the non-optional `summary: str` response field never 500s."""
    from src.corpus.schemas import CommunicationDetail, CommunicationSummary

    items, _ = await service.list_communications(status="pending", per_page=200)
    comm_c = next(i for i in items if i["nhtsa_id"] == f"{NS}-C")
    assert comm_c["summary"] == ""
    CommunicationSummary(**comm_c)  # must validate without raising

    detail = await service.get_communication(f"{NS}-C")
    assert detail["summary"] == ""
    CommunicationDetail(**detail)  # must validate without raising


async def test_tag_vocabulary_counts(seeded):
    # High limit so the fixture's rare "rotor" component is not truncated: the
    # real corpus has ~950 distinct components, well past any small top-N.
    vocab = await service.tag_vocabulary(limit=5000)
    systems = {t["tag"]: t["count"] for t in vocab["systems"]}
    assert systems.get("brakes", 0) >= 1
    assert systems.get("infotainment", 0) >= 1
    components = {t["tag"]: t["count"] for t in vocab["components"]}
    assert components.get("rotor", 0) >= 1


async def test_tag_vocabulary_merges_case_variants(seeded):
    """The LLM emits both 'electrical' and 'Electrical'; the vocabulary must
    collapse them to one entry so a chip's count matches its filter result."""
    vocab = await service.tag_vocabulary(limit=5000)
    system_tags = [t["tag"] for t in vocab["systems"]]
    # No two entries differ only by case.
    lowered = [t.lower() for t in system_tags]
    assert len(lowered) == len(set(lowered)), "case-variant systems were not merged"
