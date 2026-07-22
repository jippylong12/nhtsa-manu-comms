"""get_vehicle_details survives a flaky NHTSA (JIP-418).

Every path that asks "what does NHTSA have for this vehicle" goes through this
one call: the sync job, and the in-app refresh preview. It had no retry while
`get_safety_issue` had always had one, and NHTSA returns a 504 on this endpoint
often enough to hit one in ordinary use. A single unlucky response would fail a
preview outright, or abort a sync before it looked at one bulletin.
"""

import httpx
import pytest

from src.communications.nhtsa_client import NHTSAClient


class _Sequence:
    """An httpx transport that replays a fixed list of responses or errors."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"results": []}, request=request)


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
async def test_a_transient_504_is_retried(monkeypatch, no_sleep):
    seq = _Sequence(504, 200)
    _install(monkeypatch, seq)

    result = await NHTSAClient().get_vehicle_details(11049)

    assert result == {"results": []}
    assert seq.calls == 2, "the 504 should have been retried, not surfaced"


@pytest.mark.asyncio
async def test_rate_limiting_is_retried(monkeypatch, no_sleep):
    seq = _Sequence(403, 403, 200)
    _install(monkeypatch, seq)

    assert await NHTSAClient().get_vehicle_details(11049) == {"results": []}
    assert seq.calls == 3


@pytest.mark.asyncio
async def test_a_connection_error_is_retried(monkeypatch, no_sleep):
    seq = _Sequence(httpx.ConnectError("boom"), 200)
    _install(monkeypatch, seq)

    assert await NHTSAClient().get_vehicle_details(11049) == {"results": []}
    assert seq.calls == 2


@pytest.mark.asyncio
async def test_persistent_failure_still_raises(monkeypatch, no_sleep):
    """Retrying forever would turn an outage into a hang. Three tries, then the
    caller decides what to tell the user."""
    seq = _Sequence(504)
    _install(monkeypatch, seq)

    with pytest.raises(httpx.HTTPStatusError):
        await NHTSAClient().get_vehicle_details(11049)
    assert seq.calls == 3


@pytest.mark.asyncio
async def test_a_404_is_not_retried(monkeypatch, no_sleep):
    """An unknown vehicle will still be unknown on the third attempt."""
    seq = _Sequence(404)
    _install(monkeypatch, seq)

    with pytest.raises(httpx.HTTPStatusError):
        await NHTSAClient().get_vehicle_details(1)
    assert seq.calls == 1
