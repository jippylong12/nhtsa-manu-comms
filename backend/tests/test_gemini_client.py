"""Every Gemini call carries a timeout.

`genai.Client()` defaults to no timeout, and a stalled TLS read then blocks
forever instead of failing — one test run hung 40+ minutes parked in
`_ssl__SSLSocket_read`. These tests pin the two ways that protection could
silently come undone: a call site building its own client, and a configured
timeout the SDK quietly treats as "unbounded".
"""

import ast
import pathlib

import pytest
from google.genai._api_client import get_timeout_in_seconds
from pydantic import ValidationError

from src.config import Settings, get_settings
from src.gemini import client as gemini_client

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_the_client_applies_the_configured_timeout():
    settings = get_settings()
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not configured")

    http_options = gemini_client()._api_client._http_options
    # HttpOptions.timeout is milliseconds; the setting is seconds.
    assert http_options.timeout == settings.gemini_timeout_seconds * 1000
    # And it survives the SDK's own conversion into what httpx receives.
    assert get_timeout_in_seconds(http_options.timeout) == float(settings.gemini_timeout_seconds)


def test_a_zero_timeout_is_rejected_rather_than_meaning_unbounded():
    """The SDK reads a falsy timeout as "no timeout" (`if timeout:`), so 0 —
    the obvious way to write "no limit" — would restore the original hang."""
    assert get_timeout_in_seconds(0) is None, "SDK no longer treats 0 as unbounded; revisit gt=0"

    with pytest.raises(ValidationError):
        Settings(gemini_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(gemini_timeout_seconds=-1)


def test_no_module_builds_its_own_gemini_client():
    """Every client must come from src/gemini.py, or it has no timeout.

    A bare `genai.Client(...)` anywhere else reintroduces the unbounded case by
    omission, which is exactly how this bug arrived the first time.

    Matched on the AST rather than by regex so an import alias cannot slip past:
    `genai.Client()`, `g.Client()`, and a bare `Client()` from
    `from google.genai import Client` all parse to the same shape. Nothing else
    in `src/` constructs a `.Client(...)`, so this cannot fire spuriously.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name == "gemini.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "Client":
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")

    assert not offenders, (
        f"these build an un-timed Gemini client directly: {offenders}. "
        f"Use `from src.gemini import client` instead."
    )
