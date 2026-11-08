"""The one place that configures Stripe, so tests monkeypatch a single seam."""

import stripe

from src.config import get_settings


def client():
    """The `stripe` module with its API key set from settings."""
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set. Add it to backend/.env.")
    stripe.api_key = settings.stripe_secret_key
    return stripe
