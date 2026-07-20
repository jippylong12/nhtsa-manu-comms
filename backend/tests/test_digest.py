"""Digest watermark correctness and no-news behaviour.

The watermark rules are the whole point of the digest: items must never be
re-sent or skipped, even when a run fails between builds. These tests drive the
real schema (a seeded fixture, cleaned up) and simulate a failed send by not
advancing the watermark, exactly as the code does on error.
"""

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from src.config import get_settings
from src.jobs import digest as D

pytestmark = pytest.mark.asyncio

NS = "TESTDIGEST"
BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def seeded():
    """Three processed comms with increasing processed_at, plus a vehicle."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _cleanup(conn)
        saved = await conn.fetchval("SELECT last_watermark FROM digest_state WHERE id = true")

        v = await conn.fetchval(
            "INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model) "
            "VALUES (995001, 2099, 'CHEVROLET', $1) RETURNING id",
            f"{NS} TRUCK",
        )
        # processed_at at BASE + 1h, +2h, +3h so watermark ordering is explicit.
        for i in range(1, 4):
            cid = await conn.fetchval(
                """
                INSERT INTO communications
                    (nhtsa_id, communication_type, communication_date, summary,
                     status, processed_at)
                VALUES ($1, 'TSB', $2, $3, 'processed', $4)
                RETURNING id
                """,
                f"{NS}-{i}",
                BASE,
                f"Seeded comm {i}",
                BASE + timedelta(hours=i),
            )
            await conn.execute(
                """
                INSERT INTO comm_documents
                    (communication_id, url, extraction_method, llm_summary, systems)
                VALUES ($1, $2, 'pymupdf', $3, ARRAY['electrical'])
                """,
                cid,
                f"https://static.nhtsa.gov/{NS}-{i}.pdf",
                f"LLM summary for comm {i}",
            )
            await conn.execute(
                "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1,$2)",
                cid,
                v,
            )

        yield {"vehicle": v}
    finally:
        await _cleanup(conn)
        # Restore whatever watermark existed before the test.
        await conn.execute("UPDATE digest_state SET last_watermark = $1 WHERE id = true", saved)
        await conn.close()


async def _cleanup(conn):
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS}-%")
    await conn.execute("DELETE FROM vehicles WHERE model LIKE $1", f"{NS}%")


def _ns_items(digest):
    return [it for g in digest.groups for it in g.items if it.nhtsa_id.startswith(NS)]


async def test_build_since_none_includes_all(seeded):
    digest = await D.build_digest(since=None, generated_at=BASE)
    ids = {it.nhtsa_id for it in _ns_items(digest)}
    assert ids == {f"{NS}-1", f"{NS}-2", f"{NS}-3"}


async def test_watermark_excludes_already_seen(seeded):
    # Watermark set to BASE+2h: only comm 3 (processed at +3h) is new.
    digest = await D.build_digest(since=BASE + timedelta(hours=2), generated_at=BASE)
    ids = {it.nhtsa_id for it in _ns_items(digest)}
    assert ids == {f"{NS}-3"}


async def test_watermark_is_max_processed_at(seeded):
    digest = await D.build_digest(since=None, generated_at=BASE)
    # Ignore any real rows also processed; our seeded max is BASE+3h.
    assert digest.watermark >= BASE + timedelta(hours=3)


async def test_failed_run_then_rerun_no_dupes_no_gaps(seeded):
    """The core AC. A build that is 'sent' advances the watermark; a build whose
    send fails does not, so the next run repeats exactly the unsent items."""
    # Run 1: watermark starts before everything -> sees 1,2,3.
    start = BASE
    run1 = await D.build_digest(since=start, generated_at=BASE)
    assert {it.nhtsa_id for it in _ns_items(run1)} == {f"{NS}-1", f"{NS}-2", f"{NS}-3"}

    # Simulate the send FAILING: watermark is NOT advanced.
    # Run 2 (retry) must see the same three, no gaps.
    run2 = await D.build_digest(since=start, generated_at=BASE)
    assert {it.nhtsa_id for it in _ns_items(run2)} == {f"{NS}-1", f"{NS}-2", f"{NS}-3"}

    # Now the send SUCCEEDS: advance the watermark to run2's max.
    advanced = run2.watermark

    # Run 3: nothing new since the advanced watermark -> no NS items, no dupes.
    run3 = await D.build_digest(since=advanced, generated_at=BASE)
    assert _ns_items(run3) == []


async def test_no_news_means_no_send(seeded, monkeypatch):
    """A run with zero new items must send nothing and not call the mailer."""
    sent = []
    monkeypatch.setattr(D, "_send_email", lambda *a, **k: sent.append(a) or "id")

    # since = far future -> no items at all.
    digest = await D.run_digest(
        send=True,
        generated_at=BASE,
        since_override=BASE + timedelta(days=3650),
    )
    assert digest.has_news is False
    assert sent == [], "mailer must not be called when there is no news"


async def test_render_html_and_text_have_content(seeded):
    digest = await D.build_digest(since=None, generated_at=BASE)
    html = D.render_html(digest)
    text = D.render_text(digest)
    assert "NHTSA Comms digest" in html
    assert "LLM summary for comm 1" in html  # the summary is the headline, not the id
    assert "LLM summary for comm 1" in text
    assert "electrical" in html  # system chip rendered
    # No raw script tags reach the output (escaping is applied to content).
    assert "<script" not in html.lower()


def test_safe_link_only_allows_http():
    assert D._safe_link("https://static.nhtsa.gov/x.pdf") is True
    assert D._safe_link("http://example.com/x.pdf") is True
    assert D._safe_link("javascript:alert(1)") is False
    assert D._safe_link("data:text/html,<script>") is False
    assert D._safe_link("  JavaScript:alert(1)") is False  # trim + case
    assert D._safe_link(None) is False
    assert D._safe_link("") is False


def test_render_html_drops_dangerous_url():
    """A javascript: document URL must not become a live link."""
    d = D.Digest(
        generated_at=BASE,
        since=None,
        watermark=None,
        groups=[
            D.VehicleGroup(
                label="2099 TEST",
                items=[
                    D.DigestItem(
                        nhtsa_id="X",
                        communication_type="TSB",
                        communication_date=BASE,
                        summary="s",
                        llm_summary="a summary",
                        symptoms=[],
                        systems=[],
                        url="javascript:alert(document.cookie)",
                    )
                ],
            )
        ],
    )
    html = D.render_html(d)
    assert "javascript:" not in html
    assert "View source document" not in html  # link omitted entirely
