"""Firebase Admin: the one place an ID token is verified.

Callers use `firebase.verify(token)` through the module so tests (and the dev
fake-auth wrapper) can stub it.
"""

import base64
import json

import firebase_admin
from firebase_admin import auth, credentials

from src.config import get_settings


class AuthError(Exception):
    """The ID token is invalid, expired or revoked."""


def init_app() -> firebase_admin.App:
    """Initialise the firebase_admin app once, from FIREBASE_SERVICE_ACCOUNT_JSON."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass
    raw = get_settings().firebase_service_account_json
    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not set. Add it to backend/.env.")
    info = json.loads(base64.b64decode(raw))
    return firebase_admin.initialize_app(credentials.Certificate(info))


def verify(token: str) -> dict:
    """Verify a Firebase ID token and return its claims (`uid`, `email`, `name`, ...)."""
    init_app()
    try:
        return auth.verify_id_token(token, check_revoked=False)
    except (
        auth.InvalidIdTokenError,
        auth.ExpiredIdTokenError,
        auth.RevokedIdTokenError,
    ) as exc:
        raise AuthError(str(exc)) from exc
