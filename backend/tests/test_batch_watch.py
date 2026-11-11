"""The API's batch watch: store a late batch, then send its alerts when allowed."""

from datetime import datetime

import pytest

from src.alerts import service as alerts
from src.config import get_settings
from src.jobs import batch_watch


@pytest.fixture
def watch(monkeypatch):
    seen = {"stored": 3, "sends": []}

    async def collect_open_batches():
        return seen["stored"]

    async def run_alerts(send, generated_at, **kw):
        seen["sends"].append((send, generated_at))
        return alerts.AlertRunSummary()

    monkeypatch.setattr(batch_watch.processor, "collect_open_batches", collect_open_batches)
    monkeypatch.setattr(alerts, "run_alerts", run_alerts)
    return seen


@pytest.mark.asyncio
async def test_a_late_batch_is_followed_by_the_alert_emails_when_the_setting_is_on(
    watch, monkeypatch
):
    monkeypatch.setattr(get_settings(), "send_alerts_after_batch", True)

    assert await batch_watch.check_once() == 3

    [(send, at)] = watch["sends"]
    assert send is True and isinstance(at, datetime) and at.tzinfo is not None


@pytest.mark.asyncio
async def test_nothing_is_mailed_when_the_setting_is_off_or_nothing_was_stored(
    watch, monkeypatch
):
    monkeypatch.setattr(get_settings(), "send_alerts_after_batch", False)
    await batch_watch.check_once()
    assert watch["sends"] == []  # off by default: a local API never mails anyone

    monkeypatch.setattr(get_settings(), "send_alerts_after_batch", True)
    watch["stored"] = 0
    await batch_watch.check_once()
    assert watch["sends"] == []  # a check that stored nothing sends nothing


@pytest.mark.asyncio
async def test_a_send_already_running_or_failing_never_stops_the_watch(watch, monkeypatch):
    monkeypatch.setattr(get_settings(), "send_alerts_after_batch", True)

    async def busy(send, generated_at, **kw):
        raise alerts.AlertSendInProgress("nightly is sending")

    monkeypatch.setattr(alerts, "run_alerts", busy)
    assert await batch_watch.check_once() == 3

    async def broken(send, generated_at, **kw):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(alerts, "run_alerts", broken)
    assert await batch_watch.check_once() == 3
