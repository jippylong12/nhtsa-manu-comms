"""One place to build the Gemini client, so every request carries a timeout.

`genai.Client()` defaults to no timeout, and a stalled TLS connection then
blocks forever: the socket never returns data and never raises. That is not
hypothetical. One full test run hung for 40+ minutes with the main thread
parked in `_ssl__SSLSocket_read` and no database connections open; the suite
had no way to fail, only to sit there.

Every client in this codebase is built here so that a new call site cannot
reintroduce the unbounded case simply by forgetting the option.
"""

from google import genai
from google.genai import types

from src.config import get_settings


def client() -> genai.Client:
    """A Gemini client whose requests time out instead of hanging."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to backend/.env.")
    return genai.Client(
        api_key=settings.gemini_api_key,
        # HttpOptions.timeout is milliseconds; the setting is seconds so the
        # config surface reads in the unit a human reaches for.
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_seconds * 1000),
    )
