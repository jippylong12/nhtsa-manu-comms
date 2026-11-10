"""Stripe webhook: verify the signature, record the event once, apply it to `entitlements`.

The event row and the entitlement write share one transaction, so a handler failure
rolls the event back and Stripe's retry reprocesses it. Everything a handler trusts
comes from the signed payload. Card data and the signing secret are never logged.
A canceled subscription is terminal in Stripe, so a late or retried event never revives it.
"""

import asyncio
import contextvars
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import asyncpg
import stripe

from src.billing import stripe_client
from src.config import get_settings
from src.db import get_pool

log = logging.getLogger("billing.webhook")

# `stripe.error.SignatureVerificationError` in 9.x, `stripe.SignatureVerificationError` in 11+.
SignatureError = getattr(stripe, "SignatureVerificationError", None) or getattr(
    getattr(stripe, "error", None), "SignatureVerificationError"
)

_STATUS = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
}
_BIGINT_MAX = 2**63 - 1
PLANS = ("monthly", "all_access")


# The subscription's period end, looked up before the transaction opens (see `process`).
_looked_up_period_end: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "looked_up_period_end", default=None
)


class BadSignature(Exception):
    """The payload is not signed by the configured secret (or the header is missing)."""


def _int(value: Any) -> int | None:
    """A positive bigint from a metadata string, or None."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= _BIGINT_MAX else None


def _period_end(sub: dict) -> datetime | None:
    """`items.data[0].current_period_end` (newer API versions), else the top-level field."""
    items = (sub.get("items") or {}).get("data") or []
    epoch = (items[0].get("current_period_end") if items else None) or sub.get("current_period_end")
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _lookup_period_end(subscription_id: str) -> datetime | None:
    """Best effort: the period end of a just-started subscription, or None on any failure."""
    try:
        sub = stripe_client.client().Subscription.retrieve(subscription_id)
        return _period_end(sub)
    except Exception as e:  # a missing key, a Stripe error or an odd object must not fail the hook
        log.warning("subscription %s: period end lookup failed (%s)", subscription_id, type(e).__name__)
        return None


def _plan_of(sub: dict) -> str | None:
    """The plan a subscription is on now, read from its price id; None when unrecognised.

    This is how a plan change made in the Customer Portal reaches `entitlements`.
    """
    settings = get_settings()
    by_price = {
        settings.stripe_price_monthly: "monthly",
        settings.stripe_price_all_access: "all_access",
    }
    for item in (sub.get("items") or {}).get("data") or []:
        price = item.get("price")
        price_id = price.get("id") if isinstance(price, dict) else price
        if price_id and price_id in by_price:
            return by_price[price_id]
    return None


async def _checkout_completed(conn: asyncpg.Connection, session: dict) -> None:
    meta = session.get("metadata") or {}
    kind = meta.get("kind")
    user_id = _int(meta.get("user_id"))
    session_id = session.get("id")
    if kind == "unlock":
        paid = session.get("payment_status") == "paid"
    elif kind == "subscription":
        paid = session.get("mode") == "subscription" or session.get("payment_status") == "paid"
    else:
        log.warning("checkout %s: unknown kind, nothing written", session_id)
        return
    if not paid or not session_id or user_id is None:
        log.warning("checkout %s: not paid or no usable user id, nothing written", session_id)
        return
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE id = $1)", user_id):
        log.warning("checkout %s: user %s does not exist, nothing written", session_id, user_id)
        return
    vehicle_id = None
    subscription_id = None
    plan = None
    if kind == "subscription":
        # Checkouts started before plans existed carry no plan: they bought the $5 one.
        plan = meta.get("plan") if meta.get("plan") in PLANS else "monthly"
    if kind == "unlock":
        vehicle_id = _int(meta.get("vehicle_id"))
        if vehicle_id is None or not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM vehicles WHERE id = $1)", vehicle_id
        ):
            log.warning("checkout %s: unlock without a valid vehicle, nothing written", session_id)
            return
    else:
        subscription_id = session.get("subscription")
        if not subscription_id:
            log.warning("checkout %s: subscription without an id, nothing written", session_id)
            return
    inserted = await conn.fetchval(
        """
        INSERT INTO entitlements (user_id, kind, vehicle_id, status, stripe_customer_id,
                                  stripe_checkout_session_id, stripe_subscription_id,
                                  current_period_end, plan)
        VALUES ($1, $2, $3, 'active', $4, $5, $6, $7, $8)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        user_id, kind, vehicle_id, session.get("customer"), session_id, subscription_id,
        _looked_up_period_end.get(), plan,
    )
    if inserted and kind == "subscription":
        # Alerts are what a subscription is for: a new subscriber starts with them on,
        # even one who had turned them off under an earlier subscription.
        await conn.execute(
            "INSERT INTO alert_state (user_id) VALUES ($1) "
            "ON CONFLICT (user_id) DO UPDATE SET unsubscribed_at = NULL",
            user_id,
        )
    log.info(
        "checkout %s: user=%s kind=%s vehicle=%s entitlement=%s",
        session_id, user_id, kind, vehicle_id, inserted or "already present",
    )


async def _subscription_updated(conn: asyncpg.Connection, sub: dict) -> None:
    status = _STATUS.get(sub.get("status"))
    if status is None or not sub.get("id"):
        log.info("subscription %s: status %r not mapped, ignored", sub.get("id"), sub.get("status"))
        return
    plan = _plan_of(sub)
    result = await conn.execute(
        "UPDATE entitlements SET status = $2, "
        "current_period_end = COALESCE($3, current_period_end), plan = COALESCE($4, plan), "
        "cancel_at_period_end = $5 "
        "WHERE stripe_subscription_id = $1 AND status <> 'canceled'",
        sub["id"], status, _period_end(sub), plan,
        # Newer API versions say it with `cancel_at` (a timestamp) instead of the flag.
        bool(sub.get("cancel_at_period_end") or sub.get("cancel_at")),
    )
    log.info("subscription %s: status=%s plan=%s (%s)", sub["id"], status, plan, result)


async def _subscription_deleted(conn: asyncpg.Connection, sub: dict) -> None:
    result = await conn.execute(
        "UPDATE entitlements SET status = 'canceled' WHERE stripe_subscription_id = $1",
        sub.get("id"),
    )
    log.info("subscription %s: canceled (%s)", sub.get("id"), result)


def _invoice_subscription(invoice: dict) -> str | None:
    """`invoice.subscription` (older API versions), else `parent.subscription_details.subscription`."""
    sub = invoice.get("subscription")
    if not sub:
        details = (invoice.get("parent") or {}).get("subscription_details") or {}
        sub = details.get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return sub or None


async def _payment_failed(conn: asyncpg.Connection, invoice: dict) -> None:
    sub = _invoice_subscription(invoice)
    if not sub:
        return
    result = await conn.execute(
        "UPDATE entitlements SET status = 'past_due' "
        "WHERE stripe_subscription_id = $1 AND status <> 'canceled'",
        sub,
    )
    log.info("invoice %s: subscription %s past_due (%s)", invoice.get("id"), sub, result)


Handler = Callable[[asyncpg.Connection, dict], Awaitable[None]]
HANDLERS: dict[str, Handler] = {
    "checkout.session.completed": _checkout_completed,
    # A delayed payment method (bank debit) completes the session unpaid, then pays here.
    "checkout.session.async_payment_succeeded": _checkout_completed,
    "customer.subscription.created": _subscription_updated,
    "customer.subscription.updated": _subscription_updated,
    "customer.subscription.deleted": _subscription_deleted,
    "invoice.payment_failed": _payment_failed,
}


def verify(payload: bytes, sig_header: str | None) -> dict:
    """The event as a plain dict, or `BadSignature`."""
    if not sig_header:
        raise BadSignature("missing Stripe-Signature header")
    secret = get_settings().stripe_webhook_secret
    if not secret:
        raise BadSignature("webhook secret is not configured")
    try:
        stripe.Webhook.construct_event(payload, sig_header, secret)
    except (SignatureError, ValueError) as e:
        raise BadSignature(type(e).__name__) from None
    return json.loads(payload)


async def process(event: dict) -> dict:
    """Record the event and apply it in one transaction; `{"duplicate": True}` if seen."""
    event_id, etype = event.get("id"), event.get("type")
    if not event_id:
        raise BadSignature("event without an id")
    obj = (event.get("data") or {}).get("object") or {}
    period_end = None
    if (
        etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded")
        and (obj.get("metadata") or {}).get("kind") == "subscription"
        and isinstance(obj.get("subscription"), str)
    ):
        # A network call: made before the transaction so no connection is held while it runs.
        period_end = await asyncio.to_thread(_lookup_period_end, obj["subscription"])
    _looked_up_period_end.set(period_end)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            fresh = await conn.fetchval(
                "INSERT INTO stripe_events (event_id, type) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING RETURNING event_id",
                event_id, etype,
            )
            if fresh is None:
                log.info("event %s (%s): duplicate", event_id, etype)
                return {"duplicate": True}
            handler = HANDLERS.get(etype)
            if handler is None:
                log.info("event %s (%s): ignored", event_id, etype)
                return {"received": True}
            await handler(conn, obj)
            log.info("event %s (%s): handled", event_id, etype)
    return {"received": True}
