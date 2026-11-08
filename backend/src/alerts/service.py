"""Per-user alerts: one email a day when something new landed for the user's cars.

Generalises the single-recipient digest. Each recipient has an `alert_state` row
holding a watermark; the alert lists communications processed after it, folded to
the current revision of each bulletin and grouped by vehicle. The watermark only
advances after a send succeeds, so a failed send repeats exactly the same items on
the next run and one user's failure never stops the others.

A dry run (`send=False`) renders one HTML file per user with news and never calls
Resend or moves a watermark.
"""

import asyncio
import logging
import pathlib
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Optional

import asyncpg

from src.config import get_settings
from src.db import get_pool
from src.symptom_search.retrieval import revision_stem

log = logging.getLogger("alerts")

SEVERITY_LABELS = {"serious": "Serious", "medium": "Medium", "small": "Small"}
SEVERITY_COLORS = {"serious": "#ef4444", "medium": "#eab308", "small": "#22c55e"}


@dataclass
class AlertItem:
    nhtsa_id: str
    severity: str
    communication_type: Optional[str]
    communication_date: Optional[datetime]
    summary: str
    llm_summary: Optional[str]
    symptoms: list[str]
    systems: list[str]
    url: Optional[str]
    # Older revisions of this bulletin folded into this item.
    superseded_count: int = 0


@dataclass
class VehicleGroup:
    label: str
    items: list[AlertItem] = field(default_factory=list)


@dataclass
class ReadyVehicle:
    vehicle_id: int
    label: str
    count: int


@dataclass
class UserAlert:
    generated_at: datetime
    since: Optional[datetime]
    groups: list[VehicleGroup]
    ready_vehicles: list[ReadyVehicle]
    watermark: Optional[datetime]  # max processed_at seen

    @property
    def total_items(self) -> int:
        return sum(len(g.items) for g in self.groups)

    @property
    def has_news(self) -> bool:
        return self.total_items > 0 or bool(self.ready_vehicles)


@dataclass
class UserOutcome:
    user_id: int
    email: str
    destination: str
    items: int
    ready_vehicles: int
    outcome: str  # sent | would-send | skipped-no-news | failed
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AlertRunSummary:
    outcomes: list[UserOutcome] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(1 for o in self.outcomes if o.outcome == outcome)


async def recipients(conn, only_user_id: Optional[int] = None) -> list[dict]:
    """Subscribers with an active subscription, plus every admin; minus unsubscribed.

    Creates the user's `alert_state` row (with a fresh token) on first use. Each dict
    carries id, email, unsubscribe_token and last_watermark.
    """
    await conn.execute(
        """
        INSERT INTO alert_state (user_id)
        SELECT u.id FROM users u
        WHERE u.is_admin OR EXISTS (
            SELECT 1 FROM entitlements e
            WHERE e.user_id = u.id AND e.kind = 'subscription' AND e.status = 'active')
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    rows = await conn.fetch(
        """
        SELECT u.id, u.email, u.display_name, a.unsubscribe_token, a.last_watermark
        FROM users u
        JOIN alert_state a ON a.user_id = u.id
        WHERE a.unsubscribed_at IS NULL
          AND ($1::bigint IS NULL OR u.id = $1)
          AND (u.is_admin OR EXISTS (
              SELECT 1 FROM entitlements e
              WHERE e.user_id = u.id AND e.kind = 'subscription' AND e.status = 'active'))
        ORDER BY u.id
        """,
        only_user_id,
    )
    return [dict(r) for r in rows]


def _recency(row) -> tuple:
    """Sort key: newest communication_date first (undated last), then nhtsa_id."""
    date = row["communication_date"]
    return (date.timestamp() if date else float("-inf"), row["nhtsa_id"])


def _stem(row) -> str:
    return revision_stem(row["communication_number"]) or row["nhtsa_id"]


async def build_for_user(
    conn, user: dict, since: Optional[datetime], generated_at: datetime
) -> UserAlert:
    """Everything processed since `since` for the user's vehicles. Pure read."""
    rows = await conn.fetch(
        """
        SELECT
            c.nhtsa_id, c.communication_number, c.communication_type, c.communication_date,
            c.summary, c.severity, c.processed_at,
            v.id AS vehicle_id, v.year, v.make, v.model,
            (SELECT d.llm_summary FROM comm_documents d
             WHERE d.communication_id = c.id AND d.llm_summary IS NOT NULL
             ORDER BY d.id LIMIT 1) AS llm_summary,
            (SELECT d.url FROM comm_documents d
             WHERE d.communication_id = c.id ORDER BY d.id LIMIT 1) AS url,
            (SELECT coalesce(array_agg(DISTINCT s), '{}')
             FROM comm_documents d, unnest(d.symptoms) s
             WHERE d.communication_id = c.id) AS symptoms,
            (SELECT coalesce(array_agg(DISTINCT s), '{}')
             FROM comm_documents d, unnest(d.systems) s
             WHERE d.communication_id = c.id) AS systems
        FROM user_vehicles uv
        JOIN vehicles v ON v.id = uv.vehicle_id
        JOIN communication_vehicles cv ON cv.vehicle_id = v.id
        JOIN communications c ON c.id = cv.communication_id
        WHERE uv.user_id = $1
          AND c.status = 'processed'
          AND c.processed_at IS NOT NULL
          AND ($2::timestamptz IS NULL OR c.processed_at > $2)
        ORDER BY v.year, v.model, c.communication_date DESC NULLS LAST
        """,
        user["id"],
        since,
    )

    # Per vehicle, keep the current revision of each bulletin family and count the rest.
    families: dict[int, dict[str, list]] = {}
    labels: dict[int, str] = {}
    watermark = since
    for r in rows:
        labels[r["vehicle_id"]] = f"{r['year']} {r['make']} {r['model']}".strip()
        families.setdefault(r["vehicle_id"], {}).setdefault(_stem(r), []).append(r)
        if watermark is None or r["processed_at"] > watermark:
            watermark = r["processed_at"]

    groups: list[VehicleGroup] = []
    for vid, by_stem in families.items():
        folded = [(max(m, key=_recency), len(m) - 1) for m in by_stem.values()]
        folded.sort(key=lambda t: _recency(t[0]), reverse=True)
        groups.append(
            VehicleGroup(
                label=labels[vid],
                items=[
                    AlertItem(
                        nhtsa_id=r["nhtsa_id"],
                        severity=r["severity"],
                        communication_type=r["communication_type"],
                        communication_date=r["communication_date"],
                        summary=r["summary"] or "",
                        llm_summary=r["llm_summary"],
                        symptoms=list(r["symptoms"]),
                        systems=list(r["systems"]),
                        url=r["url"],
                        superseded_count=superseded,
                    )
                    for r, superseded in folded
                ],
            )
        )
    groups.sort(key=lambda g: g.label)

    ready = [
        ReadyVehicle(vehicle_id=vid, label=labels[vid], count=len(by_stem))
        for vid, by_stem in families.items()
        if await _first_processed_is_new(conn, vid, since)
    ]
    ready.sort(key=lambda r: r.label)

    return UserAlert(
        generated_at=generated_at,
        since=since,
        groups=groups,
        ready_vehicles=ready,
        watermark=watermark,
    )


async def _first_processed_is_new(conn, vehicle_id: int, since: Optional[datetime]) -> bool:
    """True when the vehicle's earliest processed communication is after `since`."""
    first = await conn.fetchval(
        """
        SELECT min(c.processed_at)
        FROM communication_vehicles cv JOIN communications c ON c.id = cv.communication_id
        WHERE cv.vehicle_id = $1 AND c.status = 'processed' AND c.processed_at IS NOT NULL
        """,
        vehicle_id,
    )
    return first is not None and (since is None or first > since)


def _safe_link(url: Optional[str]) -> bool:
    """True only for http(s) URLs; a stored `javascript:` URL must never become a link."""
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def _revision_note(item: AlertItem) -> str:
    """' (latest of N revisions)' when older revisions were folded into the item."""
    if item.superseded_count <= 0:
        return ""
    return f" (latest of {item.superseded_count + 1} revisions)"


def _severity(item: AlertItem) -> tuple[str, str]:
    return (
        SEVERITY_LABELS.get(item.severity, "Small"),
        SEVERITY_COLORS.get(item.severity, SEVERITY_COLORS["small"]),
    )


def _base_url() -> str:
    """APP_BASE_URL without a trailing slash, so links never get a `//` path."""
    return get_settings().app_base_url.rstrip("/")


def _footer_line() -> str:
    return f"You get this because you subscribe at {_base_url()}."


def render_text(alert: UserAlert, unsubscribe_url: str) -> str:
    """Plain-text alternative part."""
    base = _base_url()
    lines = [f"NHTSA Comms alert - {alert.generated_at:%b %d, %Y}"]
    if alert.total_items:
        lines.append(f"{alert.total_items} new communication(s) for your vehicles.")
    lines.append("")
    if alert.ready_vehicles:
        lines.append("== Now ready ==")
        for rv in alert.ready_vehicles:
            lines.append(f"- {rv.label}: {rv.count} communication(s) {base}/app/vehicles/{rv.vehicle_id}")
        lines.append("")
    for grp in alert.groups:
        lines.append(f"== {grp.label} ({len(grp.items)}) ==")
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            label, _ = _severity(it)
            lines.append(
                f"- [{label}] [{it.communication_type or 'OTHER'}] {date}{_revision_note(it)}"
            )
            lines.append(f"  {it.llm_summary or it.summary}")
            if it.systems:
                lines.append(f"  Systems: {', '.join(it.systems)}")
            if it.symptoms:
                lines.append(f"  Symptoms: {', '.join(it.symptoms[:5])}")
            if it.url:
                lines.append(f"  {it.url}")
            lines.append("")
    lines.append(_footer_line())
    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def render_html(alert: UserAlert, unsubscribe_url: str) -> str:
    """Dark-mode-friendly HTML with inline styles only (email clients strip <style>)."""
    base = _base_url()
    sections = []

    if alert.ready_vehicles:
        ready_rows = "".join(
            f'<div style="padding:6px 0;font-size:15px;color:#e5e7eb;">{escape(rv.label)} '
            f'<span style="color:#9ca3af;">({rv.count} communication{"s" if rv.count != 1 else ""})'
            f'</span> &middot; <a href="{escape(base)}/app/vehicles/{rv.vehicle_id}" '
            f'style="color:#60a5fa;text-decoration:none;">Open &rarr;</a></div>'
            for rv in alert.ready_vehicles
        )
        sections.append(
            '<tr><td style="padding-top:24px;"><div style="font-size:13px;font-weight:600;'
            'text-transform:uppercase;letter-spacing:0.05em;color:#22c55e;">Now ready</div>'
            f"{ready_rows}</td></tr>"
        )

    for grp in alert.groups:
        rows = []
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            label, color = _severity(it)
            chips = "".join(
                f'<span style="display:inline-block;font-size:11px;color:#93c5fd;'
                f"background:rgba(59,130,246,0.15);border-radius:10px;padding:2px 8px;"
                f'margin:2px 4px 2px 0;">{escape(s)}</span>'
                for s in it.systems[:4]
            )
            link = (
                f'<a href="{escape(it.url)}" style="color:#60a5fa;text-decoration:none;'
                f'font-size:13px;">View source document &rarr;</a>'
                if _safe_link(it.url)
                else ""
            )
            rows.append(
                f"""
                <tr><td style="padding:14px 0;border-bottom:1px solid #1f2937;">
                    <div style="font-size:12px;color:#9ca3af;margin-bottom:4px;">
                        <strong style="color:{color};">{label}</strong>
                        &nbsp;&middot;&nbsp;{escape(it.communication_type or 'OTHER')}
                        &nbsp;&middot;&nbsp;{date}{escape(_revision_note(it))}
                    </div>
                    <div style="font-size:15px;color:#e5e7eb;line-height:1.5;margin-bottom:6px;">
                        {escape(it.llm_summary or it.summary or 'No summary available')}
                    </div>
                    <div>{chips}</div>
                    <div style="margin-top:6px;">{link}</div>
                </td></tr>
                """
            )
        sections.append(
            f"""
            <tr><td style="padding-top:24px;">
                <div style="font-size:13px;font-weight:600;text-transform:uppercase;
                     letter-spacing:0.05em;color:#60a5fa;">{escape(grp.label)}
                     <span style="color:#6b7280;">({len(grp.items)})</span></div>
                <table width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
            </td></tr>
            """
        )

    n = alert.total_items
    count_line = (
        f" &middot; {n} new communication{'s' if n != 1 else ''} for your vehicles" if n else ""
    )
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0f17;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f17;">
<tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;
     font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <tr><td>
        <div style="font-size:22px;font-weight:700;color:#f9fafb;">NHTSA Comms alert</div>
        <div style="font-size:14px;color:#9ca3af;margin-top:4px;">
            {alert.generated_at:%B %d, %Y}{count_line}
        </div>
    </td></tr>
    {''.join(sections)}
    <tr><td style="padding-top:32px;font-size:12px;color:#6b7280;border-top:1px solid #1f2937;">
        Communications are sourced from NHTSA and summarized automatically; verify against
        the source document before acting.<br><br>
        {escape(_footer_line())}
        <a href="{escape(unsubscribe_url)}" style="color:#9ca3af;">Unsubscribe: {escape(unsubscribe_url)}</a>
    </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _send_email(to: str, subject: str, html: str, text: str) -> str:
    """Send via Resend and return the message id. Raises on misconfig or provider error."""
    import resend

    settings = get_settings()
    missing = [
        name
        for name, val in (
            ("RESEND_API_KEY", settings.resend_api_key),
            ("DIGEST_FROM_EMAIL", settings.digest_from_email),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"cannot send: missing {', '.join(missing)} in the environment")

    resend.api_key = settings.resend_api_key
    result = resend.Emails.send(
        {
            "from": settings.digest_from_email,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
    )
    return result.get("id", "") if isinstance(result, dict) else str(result)


async def run_alerts(
    send: bool,
    generated_at: datetime,
    out_dir: Optional[pathlib.Path] = None,
    only_user_id: Optional[int] = None,
    since_override: Optional[datetime] = None,
    conn: Optional[asyncpg.Connection] = None,
) -> AlertRunSummary:
    """Build each recipient's alert and send it (or render it, on a dry run).

    `generated_at` is injected so the module never reads the clock. `since_override`
    is a test-render knob and is refused together with `send`. `conn` lets a caller
    run inside its own connection (tests); by default one comes from the pool.
    """
    if send and since_override is not None:
        raise ValueError("since_override is for dry-run rendering only; never combine with send")
    if conn is not None:
        return await _locked_run(conn, send, generated_at, out_dir, only_user_id, since_override)
    pool = await get_pool()
    async with pool.acquire() as acquired:
        return await _locked_run(
            acquired, send, generated_at, out_dir, only_user_id, since_override
        )


# Session advisory lock held for the whole of a sending pass. Two passes reading the same
# watermark (the nightly cron and a manual `python -m src.alerts --send`, or a cron on
# another machine) would otherwise both mail every subscriber.
_SEND_LOCK_KEY = 0x6E68747361616C74  # "nhtsaalt"


class AlertSendInProgress(RuntimeError):
    """Another process is already sending alerts; this pass sent nothing."""


async def _locked_run(conn, send, generated_at, out_dir, only_user_id, since_override):
    if not send:
        return await _run(conn, send, generated_at, out_dir, only_user_id, since_override)
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SEND_LOCK_KEY):
        raise AlertSendInProgress("another alert send is running; this pass sent nothing")
    try:
        return await _run(conn, send, generated_at, out_dir, only_user_id, since_override)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _SEND_LOCK_KEY)


async def _run(conn, send, generated_at, out_dir, only_user_id, since_override) -> AlertRunSummary:
    settings = get_settings()
    base = _base_url()
    summary = AlertRunSummary()
    for user in await recipients(conn, only_user_id):
        since = since_override if since_override is not None else user["last_watermark"]
        destination = settings.alert_test_recipient or user["email"]
        alert = await build_for_user(conn, user, since, generated_at)
        outcome = UserOutcome(
            user_id=user["id"],
            email=user["email"],
            destination=destination,
            items=alert.total_items,
            ready_vehicles=len(alert.ready_vehicles),
            outcome="skipped-no-news",
        )
        summary.outcomes.append(outcome)
        if not alert.has_news:
            log.info("user %s: nothing new since %s", user["id"], since)
            continue

        unsubscribe_url = f"{base}/unsubscribe/{user['unsubscribe_token']}"
        html = render_html(alert, unsubscribe_url)

        if not send:
            target_dir = pathlib.Path(out_dir) if out_dir else pathlib.Path(".")
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"alert_user_{user['id']}.html"
            path.write_text(html)
            outcome.outcome = "would-send"
            log.info("DRY RUN: user %s to %s, preview %s", user["id"], destination, path)
            continue

        subject = f"NHTSA Comms: {alert.total_items} new for your vehicles"
        text = render_text(alert, unsubscribe_url)
        try:
            outcome.message_id = await asyncio.to_thread(
                _send_email, destination, subject, html, text
            )
        except Exception as exc:  # one user's failure must not stop the others
            outcome.outcome = "failed"
            outcome.error = str(exc)
            log.error("user %s: send failed: %s", user["id"], outcome.error)
            continue

        outcome.outcome = "sent"
        if alert.watermark:
            await conn.execute(
                "UPDATE alert_state SET last_watermark = $1, last_sent_at = $2 WHERE user_id = $3",
                alert.watermark,
                generated_at,
                user["id"],
            )
        log.info("user %s: sent (%d items), message id %s", user["id"], alert.total_items, outcome.message_id)
    return summary
