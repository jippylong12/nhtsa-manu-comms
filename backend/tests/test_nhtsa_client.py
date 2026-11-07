"""get_safety_issue degrades gracefully when NHTSA is unhealthy (ADR 0001, M3).

This is the only NHTSA call left in the fetch path, so its failure mode is the
whole fetch path's failure mode. A rate limit (403), a gateway error (5xx -- the
byNhtsaId endpoint 504s in exactly the unhealthy windows this project exists to
survive), or a dropped connection must all become a per-communication miss the
caller counts as a fetch failure, not an exception that aborts a vehicle's sync or
the refresh run. That is what keeps "sync never raises" and "refresh fails fast
into refresh_runs.error, never an infinite spinner" true.
"""

import httpx
import pytest

from src.communications.nhtsa_client import NHTSAClient

# A byNhtsaId success body shaped as _extract_communication expects.
COMM_BODY = {
    "results": [
        {"manufacturerCommunications": [{"nhtsaIdNumber": "11049", "summary": "brake bulletin"}]}
    ]
}


class _Sequence:
    """An httpx transport that replays a fixed list of statuses or errors."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        body = COMM_BODY if outcome == 200 else {"results": []}
        return httpx.Response(outcome, json=body, request=request)


@pytest.fixture
def no_sleep(monkeypatch):
    """Backoff is correctness, not something to wait through in a test."""
    import asyncio

    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


def _install(monkeypatch, seq: _Sequence) -> None:
    transport = httpx.MockTransport(seq.handler)
    original = httpx.AsyncClient

    def build(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", build)


@pytest.mark.asyncio
async def test_a_transient_504_is_retried_then_succeeds(monkeypatch, no_sleep):
    seq = _Sequence(504, 200)
    _install(monkeypatch, seq)

    comm = await NHTSAClient().get_safety_issue(11049)

    assert comm is not None and comm["nhtsaIdNumber"] == "11049"
    assert seq.calls == 2, "the 504 should have been retried, not surfaced"


@pytest.mark.asyncio
async def test_a_persistent_504_degrades_to_none(monkeypatch, no_sleep):
    """A gateway outage is a per-communication miss, not an exception that aborts
    the vehicle. The caller counts the None as a fetch failure."""
    seq = _Sequence(504)
    _install(monkeypatch, seq)

    assert await NHTSAClient().get_safety_issue(11049) is None
    assert seq.calls == 3


@pytest.mark.asyncio
async def test_persistent_rate_limiting_degrades_to_none(monkeypatch, no_sleep):
    seq = _Sequence(403)
    _install(monkeypatch, seq)

    assert await NHTSAClient().get_safety_issue(11049) is None
    assert seq.calls == 3


@pytest.mark.asyncio
async def test_a_connection_error_degrades_to_none(monkeypatch, no_sleep):
    seq = _Sequence(httpx.ConnectError("boom"))
    _install(monkeypatch, seq)

    assert await NHTSAClient().get_safety_issue(11049) is None
    assert seq.calls == 3


@pytest.mark.asyncio
async def test_an_empty_result_is_none_without_retrying(monkeypatch, no_sleep):
    """A 200 with no communications is a definite answer, not a transient fault."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []}, request=request)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: original(*a, **{**k, "transport": transport})
    )

    assert await NHTSAClient().get_safety_issue(999) is None
