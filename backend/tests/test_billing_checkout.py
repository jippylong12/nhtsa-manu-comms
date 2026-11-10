"""/api/billing: Checkout Sessions for the unlock and the subscription, Customer Portal (JIP-1590).

`firebase.verify` and `stripe_client.client` (a fake stripe module that records calls)
are the only faked boundaries. No real Stripe call is ever made. Runs against the real
database and deletes its own TESTBILL rows.
"""

from types import SimpleNamespace

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.auth import firebase
from src.billing import stripe_client
from src.config import get_settings
from src.main import create_app

pytestmark = pytest.mark.asyncio

PREFIX = "TESTBILL-"
USER_A = PREFIX + "a"
USER_B = PREFIX + "b"
MAKE = "TESTBILLMAKE"
YEAR = 2033
_app = create_app()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app), base_url="http://test")


def _as(uid: str) -> dict:
    return {"Authorization": f"Bearer {uid}"}


async def _wipe(conn) -> None:
    await conn.execute("DELETE FROM users WHERE firebase_uid LIKE $1", PREFIX + "%")
    await conn.execute("DELETE FROM vehicles WHERE make = $1", MAKE)


class FakeStripe:
    """Records Session.create calls and returns a canned URL."""

    def __init__(self):
        self.checkout_calls: list[dict] = []
        self.portal_calls: list[dict] = []
        outer = self

        class _Checkout:
            @staticmethod
            def create(**kwargs):
                outer.checkout_calls.append(kwargs)
                return {"url": "https://checkout.test/s/1"}

        class _Portal:
            @staticmethod
            def create(**kwargs):
                outer.portal_calls.append(kwargs)
                return {"url": "https://portal.test/p/1"}

        self.checkout = SimpleNamespace(Session=_Checkout)
        self.billing_portal = SimpleNamespace(Session=_Portal)


@pytest.fixture
def fake_stripe(monkeypatch):
    fake = FakeStripe()
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_price_unlock", "price_unlock_test")
    monkeypatch.setattr(settings, "stripe_price_monthly", "price_monthly_test")
    monkeypatch.setattr(settings, "stripe_price_all_access", "price_all_access_test")
    monkeypatch.setattr(settings, "app_base_url", "https://app.test")
    monkeypatch.setattr(stripe_client, "client", lambda: fake)
    monkeypatch.setattr(
        firebase,
        "verify",
        lambda token: {"uid": token, "email": f"{token}@example.com", "name": "T"},
    )
    return fake


@pytest_asyncio.fixture
async def db(fake_stripe):
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    conn = await asyncpg.connect(settings.database_url)
    try:
        await _wipe(conn)
        yield conn
        await _wipe(conn)
    finally:
        await conn.close()


async def _user_id(c, uid: str) -> int:
    return (await c.get("/api/me", headers=_as(uid))).json()["id"]


async def _linked_vehicle(conn, c, uid: str, model: str = "CIVIC") -> int:
    user_id = await _user_id(c, uid)
    vid = await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, $3) RETURNING id",
        YEAR, MAKE, model,
    )
    await conn.execute(
        "INSERT INTO user_vehicles (user_id, vehicle_id) VALUES ($1, $2)", user_id, vid
    )
    return vid


async def _entitlement(conn, user_id, kind, vehicle_id=None, status="active", customer=None):
    await conn.execute(
        "INSERT INTO entitlements (user_id, kind, vehicle_id, status, stripe_customer_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        user_id, kind, vehicle_id, status, customer,
    )


async def test_unlock_creates_a_payment_session_for_the_unlock_price(db, fake_stripe):
    async with _http() as c:
        vid = await _linked_vehicle(db, c, USER_A)
        uid = await _user_id(c, USER_A)
        r = await c.post(
            "/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock", "vehicleId": vid}
        )
    assert r.status_code == 200
    assert r.json() == {"url": "https://checkout.test/s/1"}
    (call,) = fake_stripe.checkout_calls
    assert call["mode"] == "payment"
    assert call["line_items"] == [{"price": "price_unlock_test", "quantity": 1}]
    assert call["metadata"] == {
        "kind": "unlock", "plan": "", "vehicle_id": str(vid), "user_id": str(uid)
    }
    assert call["client_reference_id"] == str(uid)
    assert call["customer_email"] == f"{USER_A}@example.com"
    assert "customer" not in call
    assert call["customer_creation"] == "always"
    assert call["allow_promotion_codes"] is True
    assert call["success_url"] == (
        "https://app.test/app/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert call["cancel_url"] == f"https://app.test/app/vehicles/{vid}"


async def test_unlock_for_a_vehicle_that_is_not_linked_is_404(db, fake_stripe):
    async with _http() as c:
        vid = await _linked_vehicle(db, c, USER_B)
        await _user_id(c, USER_A)
        r = await c.post(
            "/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock", "vehicleId": vid}
        )
        missing = await c.post(
            "/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock", "vehicleId": 0}
        )
        no_id = await c.post("/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock"})
    assert r.status_code == 404
    assert r.json() == missing.json()
    assert no_id.status_code == 404
    assert fake_stripe.checkout_calls == []


async def test_a_second_unlock_for_the_same_vehicle_is_409(db, fake_stripe):
    async with _http() as c:
        vid = await _linked_vehicle(db, c, USER_A)
        await _entitlement(db, await _user_id(c, USER_A), "unlock", vid)
        r = await c.post(
            "/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock", "vehicleId": vid}
        )
    assert r.status_code == 409
    assert "already unlocked" in r.json()["detail"]
    assert fake_stripe.checkout_calls == []


async def test_subscription_creates_a_subscription_session_for_the_monthly_price(db, fake_stripe):
    async with _http() as c:
        uid = await _user_id(c, USER_A)
        r = await c.post("/api/billing/checkout", headers=_as(USER_A), json={"kind": "subscription"})
    assert r.status_code == 200
    assert r.json() == {"url": "https://checkout.test/s/1"}
    (call,) = fake_stripe.checkout_calls
    assert call["mode"] == "subscription"
    assert call["line_items"] == [{"price": "price_monthly_test", "quantity": 1}]
    assert call["metadata"] == {
        "kind": "subscription", "plan": "monthly", "vehicle_id": "", "user_id": str(uid)
    }
    assert call["cancel_url"] == "https://app.test/app/account"
    assert "customer_creation" not in call  # Stripe rejects it in subscription mode


async def test_a_second_subscription_is_409(db, fake_stripe):
    async with _http() as c:
        await _entitlement(db, await _user_id(c, USER_A), "subscription")
        r = await c.post("/api/billing/checkout", headers=_as(USER_A), json={"kind": "subscription"})
    assert r.status_code == 409
    assert fake_stripe.checkout_calls == []


async def test_an_existing_stripe_customer_is_reused(db, fake_stripe):
    async with _http() as c:
        vid = await _linked_vehicle(db, c, USER_A)
        await _entitlement(
            db, await _user_id(c, USER_A), "unlock", await _other_vehicle(db), customer="cus_123"
        )
        r = await c.post(
            "/api/billing/checkout", headers=_as(USER_A), json={"kind": "unlock", "vehicleId": vid}
        )
    assert r.status_code == 200
    (call,) = fake_stripe.checkout_calls
    assert call["customer"] == "cus_123"
    assert "customer_email" not in call
    assert "customer_creation" not in call


async def _other_vehicle(conn) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (year, make, model) VALUES ($1, $2, 'OTHER') RETURNING id",
        YEAR, MAKE,
    )


async def test_portal_without_a_customer_is_404(db, fake_stripe):
    async with _http() as c:
        await _user_id(c, USER_A)
        r = await c.post("/api/billing/portal", headers=_as(USER_A))
    assert r.status_code == 404
    assert fake_stripe.portal_calls == []


async def test_portal_opens_for_the_users_customer(db, fake_stripe):
    async with _http() as c:
        await _entitlement(db, await _user_id(c, USER_A), "subscription", customer="cus_9")
        r = await c.post("/api/billing/portal", headers=_as(USER_A))
    assert r.status_code == 200
    assert r.json() == {"url": "https://portal.test/p/1"}
    assert fake_stripe.portal_calls == [
        {"customer": "cus_9", "return_url": "https://app.test/app/account"}
    ]


async def test_billing_requires_sign_in(db):
    async with _http() as c:
        r = await c.post("/api/billing/checkout", json={"kind": "subscription"})
    assert r.status_code == 401


async def test_a_missing_stripe_key_is_503_naming_the_variable(db, monkeypatch):
    monkeypatch.undo()  # drop the fake client so the real seam runs
    monkeypatch.setattr(
        firebase, "verify", lambda t: {"uid": t, "email": f"{t}@example.com", "name": "T"}
    )
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "")
    monkeypatch.setattr(get_settings(), "stripe_price_monthly", "price_monthly_test")
    async with _http() as c:
        r = await c.post("/api/billing/checkout", headers=_as(USER_A), json={"kind": "subscription"})
    assert r.status_code == 503
    assert "STRIPE_SECRET_KEY" in r.json()["detail"]


async def test_all_access_creates_a_subscription_session_for_the_all_access_price(db, fake_stripe):
    async with _http() as c:
        r = await c.post(
            "/api/billing/checkout",
            headers=_as(USER_A),
            json={"kind": "subscription", "plan": "all_access"},
        )
    assert r.status_code == 200
    (call,) = fake_stripe.checkout_calls
    assert call["mode"] == "subscription"
    assert call["line_items"] == [{"price": "price_all_access_test", "quantity": 1}]
    assert call["metadata"]["plan"] == "all_access"


async def test_a_monthly_subscriber_cannot_start_a_second_subscription_for_all_access(
    db, fake_stripe
):
    async with _http() as c:
        await _entitlement(db, await _user_id(c, USER_A), "subscription")
        r = await c.post(
            "/api/billing/checkout",
            headers=_as(USER_A),
            json={"kind": "subscription", "plan": "all_access"},
        )
    assert r.status_code == 409
    assert fake_stripe.checkout_calls == []


async def test_an_unknown_plan_is_422(db, fake_stripe):
    async with _http() as c:
        r = await c.post(
            "/api/billing/checkout",
            headers=_as(USER_A),
            json={"kind": "subscription", "plan": "free_forever"},
        )
    assert r.status_code == 422
    assert fake_stripe.checkout_calls == []


async def test_a_missing_all_access_price_is_503_naming_the_variable(db, fake_stripe, monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_price_all_access", "")
    async with _http() as c:
        r = await c.post(
            "/api/billing/checkout",
            headers=_as(USER_A),
            json={"kind": "subscription", "plan": "all_access"},
        )
    assert r.status_code == 503
    assert "STRIPE_PRICE_ALL_ACCESS" in r.json()["detail"]


async def test_cancel_opens_the_portal_on_the_cancel_page_of_the_users_subscription(
    db, fake_stripe
):
    async with _http() as c:
        uid = await _user_id(c, USER_A)
        await db.execute(
            "INSERT INTO entitlements (user_id, kind, status, stripe_customer_id, "
            "stripe_subscription_id) VALUES ($1, 'subscription', 'active', 'cus_9', $2)",
            uid, PREFIX + "sub_cancel",
        )
        r = await c.post("/api/billing/portal", headers=_as(USER_A), json={"flow": "cancel"})
    assert r.status_code == 200
    (call,) = fake_stripe.portal_calls
    assert call["customer"] == "cus_9"
    assert call["flow_data"]["type"] == "subscription_cancel"
    assert call["flow_data"]["subscription_cancel"] == {"subscription": PREFIX + "sub_cancel"}


async def test_cancel_without_a_subscription_is_404(db, fake_stripe):
    async with _http() as c:
        uid = await _user_id(c, USER_A)
        vid = await _linked_vehicle(db, c, USER_A)
        await _entitlement(db, uid, "unlock", vehicle_id=vid, customer="cus_9")
        r = await c.post("/api/billing/portal", headers=_as(USER_A), json={"flow": "cancel"})
    assert r.status_code == 404
    assert fake_stripe.portal_calls == []


async def test_cancel_never_uses_another_accounts_subscription(db, fake_stripe):
    async with _http() as c:
        other = await _user_id(c, USER_B)
        await db.execute(
            "INSERT INTO entitlements (user_id, kind, status, stripe_customer_id, "
            "stripe_subscription_id) VALUES ($1, 'subscription', 'active', 'cus_b', $2)",
            other, PREFIX + "sub_b",
        )
        uid = await _user_id(c, USER_A)
        vid = await _linked_vehicle(db, c, USER_A)
        await _entitlement(db, uid, "unlock", vehicle_id=vid, customer="cus_a")
        r = await c.post("/api/billing/portal", headers=_as(USER_A), json={"flow": "cancel"})
    assert r.status_code == 404
