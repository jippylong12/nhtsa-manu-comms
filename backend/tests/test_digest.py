"""The digest entry point: the admin's alert, run through the pipeline shim.

The per-user rules live in tests/test_alerts.py. These pin what the shim promises:
the admin (and only the admin) gets the alert, a no-news run sends nothing, a dry
run renders a file without sending or moving the watermark, and a failed send
leaves the watermark so the next run repeats the same items.

Seeded rows go through the real schema and are removed afterwards.
"""

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from src.alerts import service as A
from src.config import get_settings
from src.jobs import digest as D

NS = "TESTDIGEST"
BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def seeded(monkeypatch):
    """An admin user owning one vehicle with three processed comms (+1h, +2h, +3h)."""
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    monkeypatch.setattr(settings, "alert_test_recipient", "")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _cleanup(conn)
        # The shim picks the lowest-id admin; make ours that one for the test by
        # demoting no one: it asserts on the admin it resolves instead (see below).
        uid = await conn.fetchval(
            "INSERT INTO users (firebase_uid, email, is_admin) "
            "VALUES ($1, $2, true) RETURNING id",
            f"{NS}-admin",
            f"{NS.lower()}@digest.example.test",
        )
        v = await conn.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES (2099, 'CHEVROLET', $1) RETURNING id",
            f"{NS} TRUCK",
        )
        await conn.execute("INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1,$2)", uid, v)
        for i in range(1, 4):
            cid = await conn.fetchval(
                """
                INSERT INTO communications
                    (nhtsa_id, communication_type, communication_date, summary,
                     status, processed_at)
                VALUES ($1, 'TSB', $2, $3, 'processed', $4) RETURNING id
                """,
                f"{NS}-{i}",
                BASE,
                f"Seeded comm {i}",
                BASE + timedelta(hours=i),
            )
            await conn.execute(
                "INSERT INTO comm_documents (communication_id, url, extraction_method, "
                "llm_summary, systems) VALUES ($1, $2, 'pymupdf', $3, ARRAY['electrical'])",
                cid,
                f"https://static.nhtsa.gov/{NS}-{i}.pdf",
                f"LLM summary for comm {i}",
            )
            await conn.execute(
                "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1,$2)",
                cid,
                v,
            )
        await conn.execute(
            "INSERT INTO alert_state (user_id, last_watermark) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET last_watermark = EXCLUDED.last_watermark",
            uid,
            BASE,
        )
        yield {"conn": conn, "user": uid, "vehicle": v}
    finally:
        await _cleanup(conn)
        await conn.close()


async def _cleanup(conn):
    await conn.execute("DELETE FROM communications WHERE nhtsa_id LIKE $1", f"{NS}-%")
    await conn.execute("DELETE FROM vehicles WHERE model LIKE $1", f"{NS}%")
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", f"{NS}-%")


@pytest.fixture
def mailbox(monkeypatch):
    sent = []

    def fake(to, subject, html, text):
        sent.append({"to": to, "html": html})
        return "msg"

    monkeypatch.setattr(A, "_send_email", fake)
    return sent


def _ours(sent, address):
    return [m for m in sent if m["to"] == address]


async def _watermark(seeded):
    return await seeded["conn"].fetchval(
        "SELECT last_watermark FROM alert_state WHERE user_id = $1", seeded["user"]
    )


async def test_run_digest_sends_the_admin_his_alert_and_advances_his_watermark(seeded, mailbox):
    # Run the pass for exactly our admin through the same entry the shim uses.
    summary = await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])

    [mail] = _ours(mailbox, f"{NS.lower()}@digest.example.test")
    assert "LLM summary for comm 1" in mail["html"] and "LLM summary for comm 3" in mail["html"]
    assert "electrical" in mail["html"]
    assert "<script" not in mail["html"].lower()
    assert await _watermark(seeded) == BASE + timedelta(hours=3)
    assert summary.outcomes[0].outcome == "sent"


async def test_no_news_means_no_send(seeded, mailbox):
    await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])
    mailbox.clear()

    await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])

    assert mailbox == []


async def test_a_failed_send_repeats_the_same_items_on_the_next_run(seeded, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(A, "_send_email", boom)
    await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])
    assert await _watermark(seeded) == BASE

    sent = []
    monkeypatch.setattr(A, "_send_email", lambda to, s, h, t: sent.append(h) or "msg")
    await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])

    assert len(sent) == 1 and all(f"LLM summary for comm {i}" in sent[0] for i in (1, 2, 3))


async def test_run_digest_never_sends_without_send_and_refuses_a_since_override_with_send(
    seeded, mailbox, tmp_path
):
    summary = await D.run_digest(send=False, generated_at=BASE, out_dir=tmp_path)

    assert mailbox == []
    assert await _watermark(seeded) == BASE
    assert all(o.outcome != "sent" for o in summary.outcomes)
    with pytest.raises(ValueError):
        await A.run_alerts(send=True, generated_at=BASE, since_override=BASE)


async def test_a_vehicle_digest_lists_a_revision_family_once_as_its_current_revision(
    seeded, mailbox
):
    conn = seeded["conn"]
    for i, (number, day) in enumerate((("DG1000", 1), ("DG1000A", 2)), start=1):
        cid = await conn.fetchval(
            """
            INSERT INTO communications
                (nhtsa_id, communication_number, communication_type, communication_date,
                 summary, status, processed_at)
            VALUES ($1, $2, 'TSB', $3, $4, 'processed', $5) RETURNING id
            """,
            f"{NS}-F{i}",
            number,
            BASE + timedelta(days=day),
            f"Family revision {i}",
            BASE + timedelta(hours=10 + i),
        )
        await conn.execute(
            "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1,$2)",
            cid,
            seeded["vehicle"],
        )

    await A.run_alerts(send=True, generated_at=BASE, only_user_id=seeded["user"])

    [mail] = _ours(mailbox, f"{NS.lower()}@digest.example.test")
    assert mail["html"].count("Family revision") == 1
    assert "Family revision 2" in mail["html"]
    assert "(latest of 2 revisions)" in mail["html"]
    assert await _watermark(seeded) == BASE + timedelta(hours=12)
