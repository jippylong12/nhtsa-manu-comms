"""One place to build the Anthropic client, so every request carries a timeout.

Same reasoning as src/gemini.py: a client with no timeout can block forever on a
stalled TLS read, and a suite or a nightly run has no way to fail out of that.
The SDK retries 429s and 5xx itself (max_retries); the timeout bounds each attempt.
"""

import anthropic

from src.config import get_settings


def client() -> anthropic.Anthropic:
    """An Anthropic client whose requests time out instead of hanging."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("CLAUDE_API_KEY (or ANTHROPIC_API_KEY) is not set. Add it to backend/.env.")
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=float(settings.anthropic_timeout_seconds),
        max_retries=2,
    )
