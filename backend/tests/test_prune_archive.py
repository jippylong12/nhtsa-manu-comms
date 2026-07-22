"""Pruning the PDF archive.

The archive is a download cache with no expiry, so it grows one file per
communication forever. Pruning has to reclaim the finished ones without
touching a PDF the vision fallback still needs as live input.

Runs against the real database inside a namespaced fixture that is removed
afterwards, so the SQL is the real SQL.
"""

import pathlib
import uuid

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs.extract import archive_path, prune_archive

pytestmark = pytest.mark.asyncio

NS = f"TESTPRUNE-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def seeded(tmp_path, monkeypatch):
    """Two documents: one summarised, one still awaiting the vision fallback.

    Each gets a real file in a throwaway archive directory.
    """
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")

    monkeypatch.setattr(settings, "pdf_archive_dir", str(tmp_path))
    monkeypatch.setattr("src.jobs.extract.get_settings", lambda: settings)

    conn = await asyncpg.connect(settings.database_url)
    try:
        comm_id = await conn.fetchval(
            """
            INSERT INTO communications (nhtsa_id, communication_type, communication_date, status)
            VALUES ($1, 'TSB', '2099-01-01', 'processed') RETURNING id
            """,
            NS,
        )

        done_url = f"https://static.nhtsa.gov/{NS}-done.pdf"
        pending_url = f"https://static.nhtsa.gov/{NS}-pending.pdf"

        await conn.execute(
            """
            INSERT INTO comm_documents (communication_id, url, extracted_text, llm_summary)
            VALUES ($1, $2, 'text', 'a summary')
            """,
            comm_id,
            done_url,
        )
        # No summary yet: this is exactly the vision-fallback case, where
        # llm._vision_parts still reopens the PDF from the archive.
        await conn.execute(
            """
            INSERT INTO comm_documents (communication_id, url, extraction_method, llm_summary)
            VALUES ($1, $2, 'vision-fallback', NULL)
            """,
            comm_id,
            pending_url,
        )

        done_pdf = archive_path(done_url)
        pending_pdf = archive_path(pending_url)
        done_pdf.write_bytes(b"%PDF-1.4 done" * 100)
        pending_pdf.write_bytes(b"%PDF-1.4 pending" * 100)

        yield {"done": done_pdf, "pending": pending_pdf}
    finally:
        await conn.execute("DELETE FROM communications WHERE nhtsa_id = $1", NS)
        await conn.close()


async def test_summarised_document_pdf_is_removed(seeded):
    removed, freed = await prune_archive()

    assert not seeded["done"].exists()
    assert removed >= 1
    assert freed > 0


async def test_pdf_awaiting_vision_fallback_is_kept(seeded):
    """The one that must not regress: deleting this loses the only input the
    vision pass has, and the document can never be summarised."""
    await prune_archive()

    assert seeded["pending"].exists()


async def test_dry_run_reports_without_deleting(seeded):
    removed, freed = await prune_archive(dry_run=True)

    assert removed >= 1
    assert freed > 0
    assert seeded["done"].exists(), "dry run must not delete anything"


async def test_pruning_twice_is_harmless(seeded):
    await prune_archive()
    removed, _ = await prune_archive()

    assert not seeded["done"].exists()
    assert seeded["pending"].exists()
    # The already-deleted file is not counted a second time.
    assert removed == 0


async def test_missing_file_is_not_counted(seeded):
    seeded["done"].unlink()

    removed, freed = await prune_archive()

    assert removed == 0
    assert freed == 0


async def test_archive_path_stays_inside_the_archive_dir(tmp_path, monkeypatch):
    """Filenames come from NHTSA URLs, so traversal must not escape."""
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_archive_dir", str(tmp_path))
    monkeypatch.setattr("src.jobs.extract.get_settings", lambda: settings)

    path = archive_path("https://static.nhtsa.gov/../../../etc/passwd")

    assert pathlib.Path(tmp_path) in path.parents
