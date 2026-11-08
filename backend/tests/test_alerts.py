"""Per-user alerts: who gets mail, what is in it, and when the watermark moves.

Every test runs inside the shared `conn` fixture's transaction (rolled back at the
end), so seeded users, vehicles and communications leave no residue and watermark
moves never persist. The outbound `_send_email` is the one seam replaced; no test
reaches Resend.
"""

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.alerts import service as A
from src.config import get_settings

BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)
RUN_AT = BASE + timedelta(days=30)


class Mailbox:
    """Records sends; raises for the addresses in `fail_for`."""

    def __init__(self):
        self.sent: list[dict] = []
        self.fail_for: set[str] = set()

    def __call__(self, to, subject, html, text):
        if to in self.fail_for:
            raise RuntimeError("provider rejected the message")
        self.sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return f"msg-{len(self.sent)}"

    def to(self, address):
        return [m for m in self.sent if m["to"] == address]


@pytest.fixture
def mailbox(monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(A, "_send_email", box)
    settings = get_settings()
    monkeypatch.setattr(settings, "alert_test_recipient", "")
    monkeypatch.setattr(settings, "app_base_url", "https://app.example.test")
    return box


async def make_user(conn, tag, *, admin=False, subscription=None):
    uid = await conn.fetchval(
        "INSERT INTO users (firebase_uid, email, is_admin) VALUES ($1, $2, $3) RETURNING id",
        f"alerts-{tag}",
        f"{tag}@alerts.example.test",
        admin,
    )
    if subscription:
        await conn.execute(
            "INSERT INTO entitlements (user_id, kind, status) VALUES ($1, 'subscription', $2)",
            uid,
            subscription,
        )
    return uid, f"{tag}@alerts.example.test"


async def make_vehicle(conn, user_id, model):
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES (2099, 'TESTMAKE', $1) RETURNING id",
        model,
    )
    await conn.execute("INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2)", user_id, vid)
    return vid


async def make_comm(conn, vehicle_id, nhtsa_id, hours, *, number=None, severity="small", summary=None):
    cid = await conn.fetchval(
        """
        INSERT INTO communications
            (nhtsa_id, communication_number, communication_type, communication_date,
             summary, status, processed_at, severity)
        VALUES ($1, $2, 'TSB', $3, $4, 'processed', $5, $6) RETURNING id
        """,
        nhtsa_id,
        number,
        BASE + timedelta(days=hours),
        summary or f"NHTSA summary {nhtsa_id}",
        BASE + timedelta(hours=hours),
        severity,
    )
    await conn.execute(
        "INSERT INTO comm_documents (communication_id, url, extraction_method, llm_summary, "
        "systems) VALUES ($1, $2, 'pymupdf', $3, ARRAY['electrical'])",
        cid,
        f"https://static.nhtsa.gov/{nhtsa_id}.pdf",
        f"LLM summary {nhtsa_id}",
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid,
        vehicle_id,
    )
    return cid


async def set_watermark(conn, user_id, when):
    await conn.execute(
        "INSERT INTO alert_state (user_id, last_watermark) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET last_watermark = EXCLUDED.last_watermark",
        user_id,
        when,
    )


async def watermark(conn, user_id):
    return await conn.fetchval("SELECT last_watermark FROM alert_state WHERE user_id = $1", user_id)


def outcome_for(summary, user_id):
    return next(o for o in summary.outcomes if o.user_id == user_id)


async def test_a_subscriber_gets_one_email_with_both_new_bulletins_and_their_watermark_moves(
    conn, mailbox
):
    uid, email = await make_user(conn, "sub", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTTRUCK")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-A0", -5, number="AL0")  # already seen: car is not new
    await make_comm(conn, vid, "ALERT-A1", 1, number="AL1", severity="serious")
    await make_comm(conn, vid, "ALERT-A2", 5, number="AL2", severity="medium")

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    assert "LLM summary ALERT-A1" in mail["html"] and "LLM summary ALERT-A2" in mail["html"]
    assert mail["html"].count("2099 TESTMAKE ALERTTRUCK") == 1  # grouped under the car
    assert "Serious" in mail["html"] and "Medium" in mail["html"]
    token = await conn.fetchval("SELECT unsubscribe_token FROM alert_state WHERE user_id=$1", uid)
    assert f"https://app.example.test/unsubscribe/{token}" in mail["html"]
    assert f"https://app.example.test/unsubscribe/{token}" in mail["text"]
    assert await watermark(conn, uid) == BASE + timedelta(hours=5)
    assert outcome_for(summary, uid).outcome == "sent"
    assert outcome_for(summary, uid).items == 2


async def test_nothing_new_sends_nothing_and_leaves_the_watermark(conn, mailbox):
    uid, email = await make_user(conn, "quiet", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTQUIET")
    await make_comm(conn, vid, "ALERT-Q1", 1)
    await set_watermark(conn, uid, BASE + timedelta(hours=1))

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []
    assert await watermark(conn, uid) == BASE + timedelta(hours=1)
    assert outcome_for(summary, uid).outcome == "skipped-no-news"


@pytest.mark.parametrize("status", ["canceled", "past_due"])
async def test_a_subscriber_whose_subscription_is_not_active_gets_nothing(conn, mailbox, status):
    uid, email = await make_user(conn, "lapsed", subscription=status)
    vid = await make_vehicle(conn, uid, "ALERTLAPSED")
    await make_comm(conn, vid, "ALERT-L1", 1)

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []
    assert summary.outcomes == []


async def test_an_unsubscribed_user_gets_nothing(conn, mailbox):
    uid, email = await make_user(conn, "unsub", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTUNSUB")
    await make_comm(conn, vid, "ALERT-U1", 1)
    await conn.execute(
        "INSERT INTO alert_state (user_id, unsubscribed_at) VALUES ($1, now())", uid
    )

    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []


async def test_the_admin_gets_his_alert_without_a_subscription(conn, mailbox):
    uid, email = await make_user(conn, "boss", admin=True)
    vid = await make_vehicle(conn, uid, "ALERTADMIN")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-AD1", 1)

    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert len(mailbox.to(email)) == 1


async def subscribe_at(conn, user_id, when):
    await conn.execute(
        "UPDATE entitlements SET created_at = $2 WHERE user_id = $1 AND kind = 'subscription'",
        user_id,
        when,
    )


async def test_a_new_subscriber_with_no_watermark_only_gets_news_after_they_subscribed(
    conn, mailbox
):
    uid, email = await make_user(conn, "fresh", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTFRESH")
    await subscribe_at(conn, uid, BASE + timedelta(hours=10))
    await make_comm(conn, vid, "ALERT-F-OLD", 2, number="FR1")  # before subscribing
    await make_comm(conn, vid, "ALERT-F-NEW", 20, number="FR2")  # after subscribing

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    assert "LLM summary ALERT-F-NEW" in mail["html"]
    assert "LLM summary ALERT-F-OLD" not in mail["html"]
    assert "Now ready" not in mail["html"] and "Now ready" not in mail["text"]
    assert outcome_for(summary, uid).items == 1
    assert outcome_for(summary, uid).ready_vehicles == 0
    assert await watermark(conn, uid) == BASE + timedelta(hours=20)


async def test_a_new_subscriber_whose_cars_only_have_older_bulletins_gets_no_email(
    conn, mailbox
):
    uid, email = await make_user(conn, "stale", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTSTALE")
    await subscribe_at(conn, uid, BASE + timedelta(hours=10))
    await make_comm(conn, vid, "ALERT-ST1", 2)
    await make_comm(conn, vid, "ALERT-ST2", 9)

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []
    assert outcome_for(summary, uid).outcome == "skipped-no-news"
    assert await watermark(conn, uid) is None  # not moved backwards or forwards


async def test_a_car_first_processed_after_the_subscription_is_now_ready_once(conn, mailbox):
    uid, email = await make_user(conn, "newcar", subscription="active")
    old_car = await make_vehicle(conn, uid, "ALERTOLDCAR")
    new_car = await make_vehicle(conn, uid, "ALERTNEWCAR")
    await subscribe_at(conn, uid, BASE + timedelta(hours=10))
    await make_comm(conn, old_car, "ALERT-NC-OLD", 2, number="NC1")
    await make_comm(conn, old_car, "ALERT-NC-OLD2", 30, number="NC2")  # old car, new news
    await make_comm(conn, new_car, "ALERT-NC-NEW", 25, number="NC3")  # car ready after T

    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    assert f"/app/vehicles/{new_car}" in mail["html"]
    assert f"/app/vehicles/{old_car}" not in mail["html"]


async def test_an_admin_without_a_subscription_is_baselined_silently_on_the_first_pass(
    conn, mailbox
):
    uid, email = await make_user(conn, "baseline", admin=True)
    vid = await make_vehicle(conn, uid, "ALERTBASE")
    await make_comm(conn, vid, "ALERT-B-OLD", 2, number="BS1")  # all history, before this pass

    first = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []
    assert outcome_for(first, uid).outcome == "skipped-no-news"
    assert await watermark(conn, uid) == RUN_AT

    await make_comm(conn, vid, "ALERT-B-NEW", 24 * 31, number="BS2")  # processed after the pass
    later = RUN_AT + timedelta(days=2)
    await A.run_alerts(send=True, generated_at=later, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    assert "LLM summary ALERT-B-NEW" in mail["html"]
    assert "LLM summary ALERT-B-OLD" not in mail["html"]


async def test_a_dry_run_never_baselines_an_admin_or_lists_their_history(
    conn, mailbox, tmp_path
):
    uid, email = await make_user(conn, "drybase", admin=True)
    vid = await make_vehicle(conn, uid, "ALERTDRYBASE")
    await make_comm(conn, vid, "ALERT-DB1", 2)

    summary = await A.run_alerts(
        send=False, generated_at=RUN_AT, out_dir=tmp_path, only_user_id=uid, conn=conn
    )

    assert outcome_for(summary, uid).items == 0
    assert outcome_for(summary, uid).outcome == "skipped-no-news"
    assert await watermark(conn, uid) is None
    assert not (tmp_path / f"alert_user_{uid}.html").exists()


async def test_a_send_failure_keeps_that_watermark_and_the_next_user_still_gets_theirs(
    conn, mailbox
):
    bad, bad_email = await make_user(conn, "bad", subscription="active")
    good, good_email = await make_user(conn, "good", subscription="active")
    for uid, model in ((bad, "ALERTBAD"), (good, "ALERTGOOD")):
        vid = await make_vehicle(conn, uid, model)
        await set_watermark(conn, uid, BASE)
        await make_comm(conn, vid, f"ALERT-{model}", 3)
    mailbox.fail_for = {bad_email}

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, conn=conn)

    assert outcome_for(summary, bad).outcome == "failed"
    assert "provider rejected" in outcome_for(summary, bad).error
    assert await watermark(conn, bad) == BASE
    assert len(mailbox.to(good_email)) == 1
    assert await watermark(conn, good) == BASE + timedelta(hours=3)


async def test_a_car_processed_for_the_first_time_shows_under_now_ready_exactly_once(
    conn, mailbox
):
    uid, email = await make_user(conn, "ready", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTREADY")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-R1", 1, number="RD1")
    await make_comm(conn, vid, "ALERT-R2", 2, number="RD2")

    first = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)
    await make_comm(conn, vid, "ALERT-R3", 9, number="RD3")
    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    first_mail, second_mail = mailbox.to(email)
    assert "Now ready" in first_mail["html"] and "Now ready" in first_mail["text"]
    assert f"https://app.example.test/app/vehicles/{vid}" in first_mail["html"]
    assert outcome_for(first, uid).ready_vehicles == 1
    assert "Now ready" not in second_mail["html"]
    assert "LLM summary ALERT-R3" in second_mail["html"]


async def test_three_revisions_of_one_bulletin_render_once(conn, mailbox):
    uid, email = await make_user(conn, "rev", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTREV")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-V1", 1, number="RV100")
    await make_comm(conn, vid, "ALERT-V2", 2, number="RV100A")
    await make_comm(conn, vid, "ALERT-V3", 3, number="RV100B")

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    assert mail["html"].count("LLM summary ALERT-V") == 1
    assert "LLM summary ALERT-V3" in mail["html"]
    assert "(latest of 3 revisions)" in mail["text"]
    assert outcome_for(summary, uid).items == 1
    assert await watermark(conn, uid) == BASE + timedelta(hours=3)


async def test_the_test_recipient_setting_redirects_every_send(conn, mailbox, monkeypatch):
    uid, email = await make_user(conn, "redirect", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTREDIR")
    await make_comm(conn, vid, "ALERT-T1", 1)
    monkeypatch.setattr(get_settings(), "alert_test_recipient", "proof@alerts.example.test")

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    assert mailbox.to(email) == []
    assert len(mailbox.to("proof@alerts.example.test")) == 1
    assert outcome_for(summary, uid).destination == "proof@alerts.example.test"


async def test_a_dry_run_writes_a_file_per_user_with_news_and_never_sends_or_moves_a_watermark(
    conn, mailbox, tmp_path
):
    uid, email = await make_user(conn, "dry", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTDRY")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-D1", 4)

    summary = await A.run_alerts(
        send=False, generated_at=RUN_AT, out_dir=tmp_path, only_user_id=uid, conn=conn
    )

    assert mailbox.sent == []
    assert await watermark(conn, uid) == BASE
    assert "LLM summary ALERT-D1" in (tmp_path / f"alert_user_{uid}.html").read_text()
    assert outcome_for(summary, uid).destination == email


async def test_a_missing_resend_key_fails_that_send_by_name_and_keeps_the_watermark(
    conn, monkeypatch
):
    uid, _ = await make_user(conn, "nokey", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTNOKEY")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-N1", 2)
    settings = get_settings()
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "digest_from_email", "")

    summary = await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    outcome = outcome_for(summary, uid)
    assert outcome.outcome == "failed"
    assert "RESEND_API_KEY" in outcome.error and "DIGEST_FROM_EMAIL" in outcome.error
    assert await watermark(conn, uid) == BASE


async def test_a_second_concurrent_send_sends_nothing_and_moves_no_watermark(
    conn, mailbox
):
    uid, email = await make_user(conn, "locked", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTLOCKED")
    await set_watermark(conn, uid, BASE)
    await make_comm(conn, vid, "ALERT-L1", 2)
    other = await asyncpg.connect(get_settings().database_url)
    try:
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", A._SEND_LOCK_KEY)
        with pytest.raises(A.AlertSendInProgress):
            await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)
        assert mailbox.to(email) == []
        assert await watermark(conn, uid) == BASE
        await other.execute("SELECT pg_advisory_unlock($1)", A._SEND_LOCK_KEY)
    finally:
        await other.close()

    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)
    assert len(mailbox.to(email)) == 1
    # The lock is released after the pass, so a later pass can take it.
    assert await conn.fetchval("SELECT pg_try_advisory_lock($1)", A._SEND_LOCK_KEY)
    await conn.execute("SELECT pg_advisory_unlock($1)", A._SEND_LOCK_KEY)


async def test_a_trailing_slash_on_app_base_url_never_doubles_the_link_path(
    conn, mailbox, monkeypatch
):
    monkeypatch.setattr(get_settings(), "app_base_url", "https://app.example.test/")
    uid, email = await make_user(conn, "slash", subscription="active")
    vid = await make_vehicle(conn, uid, "ALERTSLASH")
    await make_comm(conn, vid, "ALERT-S1", 2)

    await A.run_alerts(send=True, generated_at=RUN_AT, only_user_id=uid, conn=conn)

    [mail] = mailbox.to(email)
    for part in (mail["html"], mail["text"]):
        assert "https://app.example.test/unsubscribe/" in part
        assert f"https://app.example.test/app/vehicles/{vid}" in part
        assert "example.test//" not in part


# --- Pure rendering (no database) -------------------------------------------------

UNSUB = "https://app.example.test/unsubscribe/tok123"


def _item(**over):
    base = dict(
        nhtsa_id="X1",
        severity="small",
        communication_type="TSB",
        communication_date=BASE,
        summary="s",
        llm_summary="a summary",
        symptoms=[],
        systems=[],
        url="https://static.nhtsa.gov/x.pdf",
    )
    base.update(over)
    return A.AlertItem(**base)


def _alert(*items, ready=()):
    return A.UserAlert(
        generated_at=BASE,
        since=None,
        groups=[A.VehicleGroup(label="2099 TEST CAR", items=list(items))] if items else [],
        ready_vehicles=list(ready),
        watermark=None,
    )


def test_safe_link_only_allows_http():
    assert A._safe_link("https://static.nhtsa.gov/x.pdf") is True
    assert A._safe_link("http://example.com/x.pdf") is True
    assert A._safe_link("javascript:alert(1)") is False
    assert A._safe_link("data:text/html,<script>") is False
    assert A._safe_link("  JavaScript:alert(1)") is False
    assert A._safe_link(None) is False
    assert A._safe_link("") is False


def test_a_javascript_document_url_never_becomes_a_link():
    html = A.render_html(_alert(_item(url="javascript:alert(document.cookie)")), UNSUB)

    assert "javascript:" not in html
    assert "View source document" not in html


def test_summaries_are_html_escaped():
    html = A.render_html(_alert(_item(llm_summary="<script>alert(1)</script> & more")), UNSUB)

    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html and "&amp; more" in html


@pytest.mark.parametrize(
    "severity,label,colour",
    [("serious", "Serious", "#ef4444"), ("medium", "Medium", "#eab308"), ("small", "Small", "#22c55e")],
)
def test_each_item_carries_its_severity_label_and_colour(severity, label, colour):
    alert = _alert(_item(severity=severity))

    html = A.render_html(alert, UNSUB)

    assert f'color:{colour};">{label}</strong>' in html
    assert f"[{label}]" in A.render_text(alert, UNSUB)


def test_the_unsubscribe_link_and_subscription_footer_are_in_both_parts():
    alert = _alert(_item())

    for body in (A.render_html(alert, UNSUB), A.render_text(alert, UNSUB)):
        assert UNSUB in body
        assert "You get this because you subscribe at" in body


def test_an_alert_with_nothing_in_it_does_not_claim_anything_new():
    alert = _alert()

    assert alert.has_news is False
    for body in (A.render_html(alert, UNSUB), A.render_text(alert, UNSUB)):
        assert "new communication" not in body
        assert "Now ready" not in body
