"""Business logic for Stripe Checkout and the Customer Portal.

The user id always comes from the verified session, never from the request body.
"""

from src.billing import entitlements, stripe_client
from src.config import get_settings
from src.db import get_pool


class NotFound(Exception):
    """No such vehicle on this account, or no Stripe customer."""


class Conflict(Exception):
    """The purchase is already active."""


class NotConfigured(Exception):
    """A Stripe setting is missing; the message names it."""


async def create_checkout(user: dict, kind: str, vehicle_id: int | None) -> str:
    """Create a Checkout Session for `user` and return its URL."""
    settings = get_settings()
    pool = await get_pool()
    async with pool.acquire() as conn:
        if kind == "unlock":
            linked = vehicle_id is not None and await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM user_vehicles WHERE user_id = $1 AND vehicle_id = $2)",
                user["id"],
                vehicle_id,
            )
            if not linked:
                raise NotFound("vehicle not found")
        active = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM entitlements WHERE user_id = $1 AND status = 'active' AND kind = $2
                  AND ($2 = 'subscription' OR vehicle_id = $3)
            )
            """,
            user["id"],
            kind,
            vehicle_id,
        )
        if active:
            raise Conflict("already unlocked" if kind == "unlock" else "already subscribed")
        customer = await entitlements.customer_id_for(conn, user["id"])

    price = settings.stripe_price_unlock if kind == "unlock" else settings.stripe_price_monthly
    if not price:
        raise NotConfigured(
            "STRIPE_PRICE_UNLOCK is not set" if kind == "unlock" else "STRIPE_PRICE_MONTHLY is not set"
        )
    try:
        stripe = stripe_client.client()
    except RuntimeError as e:
        raise NotConfigured(str(e)) from e

    base = settings.app_base_url.rstrip("/")
    params: dict = {
        "mode": "payment" if kind == "unlock" else "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "client_reference_id": str(user["id"]),
        "metadata": {
            "kind": kind,
            "vehicle_id": str(vehicle_id) if kind == "unlock" else "",
            "user_id": str(user["id"]),
        },
        "success_url": base + "/app/billing/success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": f"{base}/app/vehicles/{vehicle_id}" if kind == "unlock" else f"{base}/app/account",
        "allow_promotion_codes": True,
    }
    if customer:
        params["customer"] = customer
    else:
        params["customer_email"] = user["email"]
        if kind == "unlock":
            # Payment mode makes a guest (no Customer) by default, which leaves the
            # buyer without a portal and the next checkout without `customer=`.
            params["customer_creation"] = "always"
    return stripe.checkout.Session.create(**params)["url"]


async def create_portal(user: dict) -> str:
    """Create a Customer Portal session for the user's Stripe customer and return its URL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        customer = await entitlements.customer_id_for(conn, user["id"])
    if not customer:
        raise NotFound("no billing account")
    try:
        stripe = stripe_client.client()
    except RuntimeError as e:
        raise NotConfigured(str(e)) from e
    base = get_settings().app_base_url.rstrip("/")
    return stripe.billing_portal.Session.create(
        customer=customer, return_url=f"{base}/app/account"
    )["url"]
