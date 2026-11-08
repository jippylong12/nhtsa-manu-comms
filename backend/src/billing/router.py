"""/api/billing: start a Checkout Session or open the Customer Portal."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.deps import current_user
from src.billing import service, webhook
from src.billing.schemas import CheckoutRequest, UrlResponse

router = APIRouter(prefix="/billing", tags=["Billing"])


@router.post("/checkout", response_model=UrlResponse, summary="Start a Stripe Checkout Session")
async def checkout(payload: CheckoutRequest, user: dict = Depends(current_user)) -> UrlResponse:
    """The $3 vehicle unlock or the $5/month subscription; returns the hosted page URL."""
    try:
        url = await service.create_checkout(user, payload.kind, payload.vehicle_id)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except service.NotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    return UrlResponse(url=url)


@router.post("/portal", response_model=UrlResponse, summary="Open the Stripe Customer Portal")
async def portal(user: dict = Depends(current_user)) -> UrlResponse:
    """Manage or cancel; 404 when the caller has never paid."""
    try:
        url = await service.create_portal(user)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except service.NotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    return UrlResponse(url=url)


@router.post("/webhook", summary="Stripe webhook (signed; no user auth)")
async def stripe_webhook(request: Request) -> dict:
    """400 on a bad signature; a handler failure is 500 so Stripe retries."""
    payload = await request.body()
    try:
        event = webhook.verify(payload, request.headers.get("stripe-signature"))
        return await webhook.process(event)
    except webhook.BadSignature:
        raise HTTPException(status_code=400, detail="invalid signature") from None
    except Exception:
        logging.getLogger("billing.webhook").exception("webhook handler failed")
        raise HTTPException(status_code=500, detail="webhook failed") from None
