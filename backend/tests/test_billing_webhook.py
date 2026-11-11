"""/api/billing/webhook: signed, idempotent, writes entitlements (JIP-1591).

Events are posted raw with a real `Stripe-Signature` header computed by the documented
scheme (`t=<ts>,v1=hmac_sha256(secret, "<ts>.<payload>")`); the SDK's `construct_event`
verifies it. No Stripe network call is made. Deletes its own TESTHOOK rows.
"""

import hashlib
import hmac
import json
import time

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.billing import entitlements, webhook
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTHOOK-"
USER_A = PREFIX + "a"
MAKE = "TESTHOOKMAKE"
YEAR = 2034
SECRET = "whsec_test_secret"
_app = create_app()


def _http() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _sign(body: str, secret: str = SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def _post(c, event: dict, header: str | None = None):
    body = json.dumps(event)
    headers = {"Content-Type": "application/json"}
    headers["Stripe-Signature"] = header if header is not None else _sign(body)
    return await c.post("/api/billing/webhook", content=body, headers=headers)


def _event(eid: str, etype: str, obj: dict) -> dict:
    return {"id": eid, "object": "event", "type": etype, "data": {"object": obj}}


def _checkout(sid: str, user_id, vehicle_id, kind="unlock", **extra) -> dict:
    obj = {
        "id": sid,
        "object": "checkout.session",
        "mode": "payment" if kind == "unlock" else "subscription",
        "payment_status": "paid",
        "customer": "cus_" + sid,
        "subscription": None if kind == "unlock" else "sub_" + sid,
        "metadata": {
            "kind": kind,
            "user_id": str(user_id),
            "vehicle_id": "" if vehicle_id is None else str(vehicle_id),
        },
    }
    obj.update(extra)
    return obj


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM stripe_events WHERE event_id LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


@pytest_asyncio.fixture
async def db(monkeypatch):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET)
    monkeypatch.setattr(
        firebase, "verify", lambda t: {"uid": t, "email": f"{t}@example.com", "name": "T"}
    )
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe(conn)
        yield conn
        await _wipe(conn)
    finally:
        await conn.close()


async def _setup(conn, c) -> tuple[int, int]:
    uid = (await c.get("/api/me", headers={"Authorization": f"Bearer {USER_A}"})).json()["id"]
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, 'CIVIC') RETURNING id",
        YEAR, MAKE,
    )
    return uid, vid


async def _rows(conn, uid):
    return await conn.fetch("SELECT * FROM entitlements WHERE user_id = $1 ORDER BY id", uid)


async def _seen(conn, eid) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM stripe_events WHERE event_id = $1", eid))


async def test_paid_unlock_session_grants_full_access_once(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        ev = _event(PREFIX + "e1", "checkout.session.completed", _checkout(PREFIX + "s1", uid, vid))
        r = await _post(c, ev)
        again = await _post(c, ev)
    assert r.status_code == 200
    assert again.status_code == 200
    assert again.json() == {"duplicate": True}
    (row,) = await _rows(db, uid)
    assert (row["kind"], row["vehicle_id"], row["status"]) == ("unlock", vid, "active")
    assert row["stripe_customer_id"] == "cus_" + PREFIX + "s1"
    assert row["stripe_checkout_session_id"] == PREFIX + "s1"
    assert await entitlements.access_for(db, uid, vid) == "full"


async def test_bad_signature_is_400_and_records_nothing(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        ev = _event(PREFIX + "e2", "checkout.session.completed", _checkout(PREFIX + "s2", uid, vid))
        body = json.dumps(ev)
        wrong = _sign(body, "whsec_other")
        bad = await _post(c, ev, wrong)
        tampered = await _post(c, ev, _sign(body.replace("unlock", "unlocK")))
        missing = await c.post("/api/billing/webhook", content=body)
    assert (bad.status_code, tampered.status_code, missing.status_code) == (400, 400, 400)
    assert await _rows(db, uid) == []
    assert not await _seen(db, PREFIX + "e2")


async def test_subscription_session_stores_customer_and_subscription_without_vehicle(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sid = PREFIX + "s3"
        r = await _post(
            c, _event(PREFIX + "e3", "checkout.session.completed", _checkout(sid, uid, None, "subscription"))
        )
    assert r.status_code == 200
    (row,) = await _rows(db, uid)
    assert (row["kind"], row["vehicle_id"], row["status"]) == ("subscription", None, "active")
    assert row["stripe_customer_id"] == "cus_" + sid
    assert row["stripe_subscription_id"] == "sub_" + sid
    assert row["plan"] == "monthly"  # a checkout without a plan bought the $5 one


async def _subscribe(c, uid, tag):
    sid = PREFIX + tag
    await _post(
        c, _event(PREFIX + "c" + tag, "checkout.session.completed", _checkout(sid, uid, None, "subscription"))
    )
    return "sub_" + sid


async def test_subscription_updated_maps_status_and_period_end(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sub = await _subscribe(c, uid, "s4")
        past = _event(
            PREFIX + "e4a", "customer.subscription.updated",
            {"id": sub, "status": "past_due", "items": {"data": [{"current_period_end": 1900000000}]}},
        )
        await _post(c, past)
        (row,) = await _rows(db, uid)
        assert row["status"] == "past_due"
        assert int(row["current_period_end"].timestamp()) == 1900000000
        assert await entitlements.access_for(db, uid, vid) == "sample"
        back = _event(
            PREFIX + "e4b", "customer.subscription.updated",
            {"id": sub, "status": "trialing", "current_period_end": 1950000000},
        )
        await _post(c, back)
    (row,) = await _rows(db, uid)
    assert row["status"] == "active"
    assert int(row["current_period_end"].timestamp()) == 1950000000


async def test_subscription_deleted_cancels_and_access_is_sample(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sub = await _subscribe(c, uid, "s5")
        assert await entitlements.access_for(db, uid, vid) == "full"
        r = await _post(c, _event(PREFIX + "e5", "customer.subscription.deleted", {"id": sub}))
    assert r.status_code == 200
    (row,) = await _rows(db, uid)
    assert row["status"] == "canceled"
    assert await entitlements.access_for(db, uid, vid) == "sample"


async def test_invoice_payment_failed_marks_past_due(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sub = await _subscribe(c, uid, "s6")
        await _post(c, _event(PREFIX + "e6", "invoice.payment_failed", {"id": "in_1", "subscription": sub}))
    (row,) = await _rows(db, uid)
    assert row["status"] == "past_due"


async def test_unknown_type_is_200_and_recorded(db):
    async with _http() as c:
        r = await _post(c, _event(PREFIX + "e7", "charge.refunded", {"id": "ch_1"}))
    assert r.status_code == 200
    assert await _seen(db, PREFIX + "e7")


async def test_subscription_event_for_unknown_subscription_is_harmless(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        r = await _post(c, _event(PREFIX + "e8", "customer.subscription.deleted", {"id": "sub_nope"}))
    assert r.status_code == 200
    assert await _rows(db, uid) == []


@pytest.mark.parametrize(
    "meta",
    [
        {"kind": "unlock", "vehicle_id": "{vid}"},  # no user_id
        {"kind": "unlock", "user_id": "abc", "vehicle_id": "{vid}"},
        {"kind": "unlock", "user_id": "999999999999", "vehicle_id": "{vid}"},  # no such user
        {"kind": "bogus", "user_id": "{uid}", "vehicle_id": "{vid}"},
        {"kind": "unlock", "user_id": "{uid}", "vehicle_id": ""},
        {"kind": "unlock", "user_id": "{uid}", "vehicle_id": "999999999999"},
    ],
)
async def test_bad_metadata_records_the_event_but_writes_no_entitlement(db, meta):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        meta = {k: v.format(uid=uid, vid=vid) for k, v in meta.items()}
        session = _checkout(PREFIX + "s9", uid, vid)
        session["metadata"] = meta
        r = await _post(c, _event(PREFIX + "e9", "checkout.session.completed", session))
    assert r.status_code == 200
    assert await _rows(db, uid) == []
    assert await _seen(db, PREFIX + "e9")


async def test_unpaid_payment_session_grants_nothing(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        session = _checkout(PREFIX + "s10", uid, vid, payment_status="unpaid")
        r = await _post(c, _event(PREFIX + "e10", "checkout.session.completed", session))
    assert r.status_code == 200
    assert await _rows(db, uid) == []


async def test_handler_failure_rolls_back_the_event_and_is_5xx(db, monkeypatch):
    async def boom(conn, obj):
        raise RuntimeError("db hiccup")

    monkeypatch.setitem(webhook.HANDLERS, "checkout.session.completed", boom)
    async with _http() as c:
        uid, vid = await _setup(db, c)
        r = await _post(
            c, _event(PREFIX + "e11", "checkout.session.completed", _checkout(PREFIX + "s11", uid, vid))
        )
    assert r.status_code == 500
    assert not await _seen(db, PREFIX + "e11")


async def test_invoice_payment_failed_reads_the_subscription_from_parent_on_newer_api(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sub = await _subscribe(c, uid, "s12")
        invoice = {"id": "in_2", "parent": {"subscription_details": {"subscription": sub}}}
        await _post(c, _event(PREFIX + "e12", "invoice.payment_failed", invoice))
    (row,) = await _rows(db, uid)
    assert row["status"] == "past_due"


async def test_a_late_event_never_revives_a_canceled_subscription(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sub = await _subscribe(c, uid, "s13")
        await _post(c, _event(PREFIX + "e13a", "customer.subscription.deleted", {"id": sub}))
        await _post(
            c,
            _event(PREFIX + "e13b", "customer.subscription.updated", {"id": sub, "status": "active"}),
        )
        await _post(c, _event(PREFIX + "e13c", "invoice.payment_failed", {"id": "in_3", "subscription": sub}))
    (row,) = await _rows(db, uid)
    assert row["status"] == "canceled"
    assert await entitlements.access_for(db, uid, vid) == "sample"


async def test_a_delayed_payment_unlocks_once_when_it_succeeds(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sid = PREFIX + "s14"
        unpaid = _checkout(sid, uid, vid, payment_status="unpaid")
        await _post(c, _event(PREFIX + "e14a", "checkout.session.completed", unpaid))
        assert await _rows(db, uid) == []
        paid = _checkout(sid, uid, vid)
        r = await _post(c, _event(PREFIX + "e14b", "checkout.session.async_payment_succeeded", paid))
        await _post(c, _event(PREFIX + "e14c", "checkout.session.async_payment_succeeded", paid))
    assert r.status_code == 200
    (row,) = await _rows(db, uid)
    assert (row["kind"], row["vehicle_id"], row["status"]) == ("unlock", vid, "active")
    assert await entitlements.access_for(db, uid, vid) == "full"


class _FakeStripe:
    """The stripe seam with only `Subscription.retrieve`, recording the ids asked for."""

    def __init__(self, result=None, error: Exception | None = None):
        self.asked: list[str] = []
        outer = self

        class Subscription:
            @staticmethod
            def retrieve(sub_id):
                outer.asked.append(sub_id)
                if error is not None:
                    raise error
                return result

        self.Subscription = Subscription


def _seam(monkeypatch, fake: _FakeStripe) -> None:
    from src.billing import stripe_client

    monkeypatch.setattr(stripe_client, "client", lambda: fake)


async def test_subscription_checkout_stores_the_period_end_from_stripe(db, monkeypatch):
    fake = _FakeStripe({"id": "x", "items": {"data": [{"current_period_end": 1900000000}]}})
    _seam(monkeypatch, fake)
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sid = PREFIX + "s10"
        r = await _post(
            c, _event(PREFIX + "e10", "checkout.session.completed", _checkout(sid, uid, None, "subscription"))
        )
    assert r.status_code == 200
    assert fake.asked == ["sub_" + sid]
    (row,) = await _rows(db, uid)
    assert row["status"] == "active"
    assert int(row["current_period_end"].timestamp()) == 1900000000


async def test_a_failed_subscription_lookup_still_writes_the_entitlement(db, monkeypatch):
    _seam(monkeypatch, _FakeStripe(error=RuntimeError("STRIPE_SECRET_KEY is not set")))
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sid = PREFIX + "s11"
        r = await _post(
            c, _event(PREFIX + "e11", "checkout.session.completed", _checkout(sid, uid, None, "subscription"))
        )
    assert r.status_code == 200
    (row,) = await _rows(db, uid)
    assert (row["kind"], row["status"], row["current_period_end"]) == ("subscription", "active", None)
    assert row["stripe_subscription_id"] == "sub_" + sid
    assert await entitlements.access_for(db, uid, vid) == "full"


async def test_subscription_created_sets_status_and_period_end(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sub = await _subscribe(c, uid, "s12")
        r = await _post(
            c, _event(
                PREFIX + "e12", "customer.subscription.created",
                {"id": sub, "status": "active", "items": {"data": [{"current_period_end": 1900000000}]}},
            ),
        )
    assert r.status_code == 200
    (row,) = await _rows(db, uid)
    assert row["status"] == "active"
    assert int(row["current_period_end"].timestamp()) == 1900000000


async def test_subscription_created_for_an_unknown_subscription_changes_nothing(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        r = await _post(
            c, _event(PREFIX + "e13", "customer.subscription.created", {"id": "sub_nope", "status": "active"})
        )
    assert r.status_code == 200
    assert await _rows(db, uid) == []


async def test_an_all_access_checkout_stores_the_plan(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        obj = _checkout(PREFIX + "p1", uid, None, "subscription")
        obj["metadata"]["plan"] = "all_access"
        await _post(c, _event(PREFIX + "ep1", "checkout.session.completed", obj))
    (row,) = await _rows(db, uid)
    assert row["plan"] == "all_access"


async def test_a_plan_the_app_does_not_sell_is_stored_as_monthly(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        obj = _checkout(PREFIX + "p2", uid, None, "subscription")
        obj["metadata"]["plan"] = "enterprise"
        await _post(c, _event(PREFIX + "ep2", "checkout.session.completed", obj))
    (row,) = await _rows(db, uid)
    assert row["plan"] == "monthly"


async def test_an_unlock_has_no_plan(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        await _post(
            c, _event(PREFIX + "ep3", "checkout.session.completed", _checkout(PREFIX + "p3", uid, vid))
        )
    (row,) = await _rows(db, uid)
    assert row["plan"] is None


async def test_a_plan_change_in_the_portal_moves_the_entitlement_to_the_new_plan(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_price_monthly", "price_m")
    monkeypatch.setattr(settings, "stripe_price_all_access", "price_a")
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sub = await _subscribe(c, uid, "p4")
        up = {"id": sub, "status": "active", "items": {"data": [{"price": {"id": "price_a"}}]}}
        await _post(c, _event(PREFIX + "ep4a", "customer.subscription.updated", up))
        (row,) = await _rows(db, uid)
        assert row["plan"] == "all_access"
        # An event whose price the app does not know leaves the plan alone.
        odd = {"id": sub, "status": "active", "items": {"data": [{"price": {"id": "price_x"}}]}}
        await _post(c, _event(PREFIX + "ep4b", "customer.subscription.updated", odd))
        (row,) = await _rows(db, uid)
        assert row["plan"] == "all_access"
        down = {"id": sub, "status": "active", "items": {"data": [{"price": "price_m"}]}}
        await _post(c, _event(PREFIX + "ep4c", "customer.subscription.updated", down))
    (row,) = await _rows(db, uid)
    assert row["plan"] == "monthly"


async def test_a_cancelled_subscription_is_marked_as_ending_and_can_be_resumed(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        sub = await _subscribe(c, uid, "c1")
        ending = {"id": sub, "status": "active", "cancel_at_period_end": True}
        await _post(c, _event(PREFIX + "ec1a", "customer.subscription.updated", ending))
        (row,) = await _rows(db, uid)
        assert row["cancel_at_period_end"] is True and row["status"] == "active"
        assert await entitlements.access_for(db, uid, vid) == "full"  # paid through the period
        newer = {"id": sub, "status": "active", "cancel_at": 1900000000}
        await _post(c, _event(PREFIX + "ec1b", "customer.subscription.updated", newer))
        (row,) = await _rows(db, uid)
        assert row["cancel_at_period_end"] is True
        resumed = {"id": sub, "status": "active", "cancel_at_period_end": False}
        await _post(c, _event(PREFIX + "ec1c", "customer.subscription.updated", resumed))
    (row,) = await _rows(db, uid)
    assert row["cancel_at_period_end"] is False


async def test_subscribing_turns_alerts_back_on(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        await db.execute(
            "INSERT INTO alert_state (user_id, unsubscribed_at) VALUES ($1, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET unsubscribed_at = now()",
            uid,
        )
        await _subscribe(c, uid, "a1")
    assert await db.fetchval(
        "SELECT unsubscribed_at FROM alert_state WHERE user_id = $1", uid
    ) is None


async def test_an_unlock_does_not_touch_the_alert_setting(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        await db.execute(
            "INSERT INTO alert_state (user_id, unsubscribed_at) VALUES ($1, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET unsubscribed_at = now()",
            uid,
        )
        await _post(
            c, _event(PREFIX + "ea2", "checkout.session.completed", _checkout(PREFIX + "a2", uid, vid))
        )
    assert await db.fetchval(
        "SELECT unsubscribed_at FROM alert_state WHERE user_id = $1", uid
    ) is not None


# --- a payment starts the account's symptom searches again (migration 0024) --------------


async def _reset_at(conn, uid):
    return await conn.fetchval("SELECT searches_reset_at FROM users WHERE id = $1", uid)


async def test_every_purchase_resets_the_search_count_and_a_replay_does_not(db):
    async with _http() as c:
        uid, vid = await _setup(db, c)
        assert await _reset_at(db, uid) is None
        first = _event(
            PREFIX + "q1", "checkout.session.completed", _checkout(PREFIX + "q1", uid, vid, "unlock")
        )
        await _post(c, first)
        after_first = await _reset_at(db, uid)
        assert after_first is not None

        await _post(c, first)  # Stripe sends the same event again
        assert await _reset_at(db, uid) == after_first

        other = await db.fetchval(
            "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, 'ACCORD') RETURNING id",
            YEAR, MAKE,
        )
        await _post(
            c,
            _event(
                PREFIX + "q2", "checkout.session.completed",
                _checkout(PREFIX + "q2", uid, other, "unlock"),
            ),
        )
        assert await _reset_at(db, uid) > after_first  # a second car, weeks later


async def test_a_subscription_renewal_resets_the_search_count_once(db):
    async with _http() as c:
        uid, _ = await _setup(db, c)
        sub = await _subscribe(c, uid, "q3")

        def updated(eid, period_end, status="active"):
            return _event(
                PREFIX + eid, "customer.subscription.updated",
                {"id": sub, "status": status, "current_period_end": period_end},
            )

        await _post(c, updated("q3a", 1900000000))  # the first period is recorded
        start = await _reset_at(db, uid)

        await _post(c, updated("q3b", 1900000000))  # an update inside the same period
        assert await _reset_at(db, uid) == start

        await _post(c, updated("q3c", 1902600000))  # next month: Stripe moves the period on
        renewed = await _reset_at(db, uid)
        assert renewed > start

        await _post(c, updated("q3d", 1905200000, status="past_due"))  # unpaid: no reset
        assert await _reset_at(db, uid) == renewed
