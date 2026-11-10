"""Per-user alerts: one email a day when something new landed for the user's cars.

Generalises the single-recipient digest. Each recipient has an `alert_state` row
holding a watermark; the alert lists communications processed after it, folded to
the current revision of each bulletin and grouped by vehicle. The watermark only
advances after a send succeeds, so a failed send repeats exactly the same items on
the next run and one user's failure never stops the others.

A user with no watermark yet is not "everything is new": the baseline is the creation of
their earliest active subscription, or, for an admin with no subscription, the first pass
that sees them (that pass sends nothing and stores the moment as their watermark). So a
first alert only carries news that arrived after the user became a recipient.

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
from urllib.parse import quote

import asyncpg

from src.billing.entitlements import access_for
from src.config import get_settings
from src.db import get_pool
from src.symptom_search.retrieval import revision_stem
from src.vehicles.display_name import display_vehicle

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
    # 0 when unknown: the email then leaves out the links into the app for this car.
    vehicle_id: int = 0


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
    carries id, email, unsubscribe_token, last_watermark and subscribed_since (when the
    user's earliest active subscription was created; None for an admin without one).
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
        SELECT u.id, u.email, u.display_name, a.unsubscribe_token, a.last_watermark,
               (SELECT min(e.created_at) FROM entitlements e
                WHERE e.user_id = u.id AND e.kind = 'subscription' AND e.status = 'active'
               ) AS subscribed_since
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


def baseline_for(user: dict) -> Optional[datetime]:
    """Where a user's news starts: their watermark, else when they subscribed, else None.

    None means an admin with no watermark and no subscription: the caller baselines them
    at the moment of the pass instead of treating their history as news.
    """
    return user["last_watermark"] or user.get("subscribed_since")


async def build_for_user(
    conn, user: dict, since: Optional[datetime], generated_at: datetime
) -> UserAlert:
    """Everything processed since `since` for the user's vehicles. Pure read.

    Only cars the account reads in full: a car past the plan's count (after a move to a
    smaller plan) is a sample, and an alert would hand out its new bulletins in full.
    """
    linked = await conn.fetch(
        "SELECT vehicle_id FROM user_vehicles WHERE user_id = $1 AND alerts_enabled", user["id"]
    )
    readable = [
        r["vehicle_id"]
        for r in linked
        if await access_for(conn, user["id"], r["vehicle_id"]) == "full"
    ]
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
          AND uv.alerts_enabled
          AND uv.vehicle_id = ANY($3::bigint[])
          AND c.status = 'processed'
          AND c.processed_at IS NOT NULL
          AND ($2::timestamptz IS NULL OR c.processed_at > $2)
        ORDER BY v.year, v.model, c.communication_date DESC NULLS LAST
        """,
        user["id"],
        since,
        readable,
    )

    # Per vehicle, keep the current revision of each bulletin family and count the rest.
    families: dict[int, dict[str, list]] = {}
    labels: dict[int, str] = {}
    watermark = since
    for r in rows:
        labels[r["vehicle_id"]] = display_vehicle(r["year"], r["make"], r["model"])
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
                vehicle_id=vid,
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


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def subject_for(alert: UserAlert) -> str:
    """The subject line: what is new, in the owner's words."""
    n = alert.total_items
    cars = [g.label for g in alert.groups] or [rv.label for rv in alert.ready_vehicles]
    one = cars[0] if len(cars) == 1 else None
    if n:
        return f"{_plural(n, 'new bulletin')} for your {one or 'cars'}"
    return f"Your {one} is ready to read" if one else "Your cars are ready to read"


def _item_url(base: str, vehicle_id: int, item: AlertItem) -> str:
    """The bulletin opened in the app, where "how to tell if your car has this" lives."""
    return f"{base}/app/vehicles/{vehicle_id}?b={quote(item.nhtsa_id, safe='')}"


def render_text(alert: UserAlert, unsubscribe_url: str) -> str:
    """Plain-text alternative part."""
    base = _base_url()
    lines = [f"Not Just My Car - {alert.generated_at:%b %d, %Y}"]
    if alert.total_items:
        lines.append(f"{_plural(alert.total_items, 'new bulletin')} for your cars.")
    lines.append("")
    if alert.ready_vehicles:
        lines.append("== Now ready ==")
        for rv in alert.ready_vehicles:
            lines.append(
                f"- {rv.label}: {_plural(rv.count, 'bulletin')} {base}/app/vehicles/{rv.vehicle_id}"
            )
        lines.append("")
    for grp in alert.groups:
        lines.append(f"== {grp.label} ({len(grp.items)}) ==")
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            label, _ = _severity(it)
            lines.append(f"- [{label}] {date}{_revision_note(it)}")
            lines.append(f"  {it.llm_summary or it.summary}")
            if it.systems:
                lines.append(f"  Systems: {', '.join(it.systems)}")
            if it.symptoms:
                lines.append(f"  Symptoms: {', '.join(it.symptoms[:5])}")
            if grp.vehicle_id:
                lines.append(f"  See if your car has this: {_item_url(base, grp.vehicle_id, it)}")
            if it.url:
                lines.append(f"  Original document: {it.url}")
            lines.append("")
    lines.append(_footer_line())
    lines.append(f"Manage alerts: {base}/app/account")
    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _logo_html() -> str:
    """The brand mark drawn with table cells (email clients drop SVG), beside the wordmark."""
    rows = "".join(
        f'<tr><td style="padding:2px 4px 2px 0;"><div style="width:7px;height:7px;'
        f'border-radius:4px;background:{dot};font-size:0;line-height:0;">&nbsp;</div></td>'
        f'<td style="padding:2px 0;"><div style="width:{bar}px;height:5px;border-radius:3px;'
        f'background:#f8fafc;opacity:{alpha};font-size:0;line-height:0;">&nbsp;</div></td></tr>'
        for dot, bar, alpha in (("#ef4444", 16, 1), ("#f5b400", 11, 0.82), ("#34c777", 14, 0.64))
    )
    return (
        '<table cellpadding="0" cellspacing="0" role="presentation"><tr>'
        '<td style="width:40px;height:40px;background:#16233f;border:1px solid #3b82f6;'
        'border-radius:10px;padding:0 0 0 7px;" valign="middle">'
        f'<table cellpadding="0" cellspacing="0" role="presentation">{rows}</table></td>'
        f'<td style="padding-left:12px;font-family:{_FONT};font-size:20px;letter-spacing:-0.3px;" '
        'valign="middle"><span style="font-weight:700;color:#f8fafc;">Not Just</span> '
        '<span style="font-weight:500;color:#8fb4ff;">My Car</span></td>'
        "</tr></table>"
    )


def _button(href: str, label: str) -> str:
    return (
        f'<a href="{escape(href)}" style="display:inline-block;background:#3b82f6;color:#ffffff;'
        f"font-size:14px;font-weight:600;text-decoration:none;padding:10px 16px;"
        f'border-radius:8px;">{escape(label)} &rarr;</a>'
    )


def render_html(alert: UserAlert, unsubscribe_url: str) -> str:
    """The alert email. Inline styles and tables only: email clients strip <style> and SVG."""
    base = _base_url()
    sections = []

    if alert.ready_vehicles:
        ready_rows = "".join(
            f'<div style="padding:8px 0;font-size:15px;color:#e5e7eb;">'
            f'<strong style="color:#f8fafc;">{escape(rv.label)}</strong> '
            f'<span style="color:#9ca3af;">&middot; {_plural(rv.count, "bulletin")} on file</span> '
            f'&middot; <a href="{escape(base)}/app/vehicles/{rv.vehicle_id}" '
            f'style="color:#8fb4ff;text-decoration:none;font-weight:600;">Open &rarr;</a></div>'
            for rv in alert.ready_vehicles
        )
        sections.append(
            '<tr><td style="padding:20px 0 0;"><table width="100%" cellpadding="0" cellspacing="0" '
            'role="presentation" style="background:#10231b;border:1px solid #1f5137;'
            'border-radius:12px;"><tr><td style="padding:16px 18px;">'
            '<div style="font-size:12px;font-weight:700;text-transform:uppercase;'
            'letter-spacing:0.06em;color:#34c777;">Now ready</div>'
            f"{ready_rows}</td></tr></table></td></tr>"
        )

    for grp in alert.groups:
        cards = []
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            label, color = _severity(it)
            chips = "".join(
                f'<span style="display:inline-block;font-size:12px;color:#aac4f5;'
                f"background:#1a2740;border-radius:10px;padding:3px 9px;"
                f'margin:0 6px 6px 0;">{escape(s)}</span>'
                for s in it.systems[:4]
            )
            actions = []
            if grp.vehicle_id:
                actions.append(
                    _button(_item_url(base, grp.vehicle_id, it), "See if your car has this")
                )
            if _safe_link(it.url):
                actions.append(
                    f'<a href="{escape(it.url)}" style="display:inline-block;color:#8fb4ff;'
                    f'font-size:13px;text-decoration:none;padding:10px 0 10px '
                    f'{14 if actions else 0}px;">Original document (PDF)</a>'
                )
            cards.append(
                f"""
                <tr><td style="padding:0 0 12px;">
                <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                       style="background:#141c2e;border:1px solid #243049;
                              border-left:4px solid {color};border-radius:12px;">
                <tr><td style="padding:16px 18px;">
                    <div style="font-size:13px;color:#9ca3af;margin-bottom:8px;">
                        <strong style="color:{color};">{label}</strong>
                        &nbsp;&middot;&nbsp;{date}{escape(_revision_note(it))}
                    </div>
                    <div style="font-size:16px;color:#e8edf5;line-height:1.55;margin-bottom:12px;">
                        {escape(it.llm_summary or it.summary or 'No summary available')}
                    </div>
                    <div>{chips}</div>
                    <div style="margin-top:6px;">{''.join(actions)}</div>
                </td></tr></table>
                </td></tr>
                """
            )
        open_car = (
            f'<a href="{escape(base)}/app/vehicles/{grp.vehicle_id}" style="color:#8fb4ff;'
            f'font-size:13px;text-decoration:none;font-weight:600;">Open this car &rarr;</a>'
            if grp.vehicle_id
            else ""
        )
        sections.append(
            f"""
            <tr><td style="padding:28px 0 12px;">
                <table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>
                <td style="font-size:19px;font-weight:700;color:#f8fafc;">{escape(grp.label)}
                    <span style="font-size:13px;font-weight:500;color:#9ca3af;">
                    &nbsp;{len(grp.items)} new</span></td>
                <td align="right" style="white-space:nowrap;">{open_car}</td>
                </tr></table>
            </td></tr>
            {''.join(cards)}
            """
        )

    n = alert.total_items
    if n:
        whose = "car" if len(alert.groups) == 1 else "cars"
        headline = f"{_plural(n, 'new bulletin')} for your {whose}"
        lede = (
            "Your carmaker sent these to its dealers. Here is what each one means for you."
        )
    elif alert.ready_vehicles:
        headline, lede = "Your car is ready to read", "We have finished reading its bulletins."
    else:
        headline, lede = "Nothing to report", "There is nothing for your cars today."
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{escape(headline)}</title></head>
<body style="margin:0;padding:0;background:#0b1120;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(lede)}</div>
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#0b1120;">
<tr><td align="center" style="padding:28px 14px 40px;">
<table width="600" cellpadding="0" cellspacing="0" role="presentation"
       style="width:100%;max-width:600px;font-family:{_FONT};">
    <tr><td style="padding-bottom:24px;">{_logo_html()}</td></tr>
    <tr><td>
        <div style="font-size:26px;line-height:1.25;font-weight:700;color:#f8fafc;
                    letter-spacing:-0.4px;">{escape(headline)}</div>
        <div style="font-size:15px;line-height:1.5;color:#9ca3af;margin-top:6px;">
            {escape(lede)} <span style="white-space:nowrap;">{alert.generated_at:%B %d, %Y}</span>
        </div>
    </td></tr>
    {''.join(sections)}
    <tr><td style="padding-top:28px;">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="border-top:1px solid #1f2a3d;"><tr>
        <td style="padding-top:18px;font-size:12px;line-height:1.6;color:#6b7686;">
            Bulletins come from NHTSA's public files and are summarized automatically. Check the
            original document before acting on one.<br><br>
            {escape(_footer_line())}<br>
            <a href="{escape(base)}/app/account" style="color:#9ca3af;">Manage alerts</a>
            &nbsp;&middot;&nbsp;
            <a href="{escape(unsubscribe_url)}" style="color:#9ca3af;">Unsubscribe</a>
        </td></tr></table>
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
        since = since_override if since_override is not None else baseline_for(user)
        first_pass = since is None
        if first_pass:
            # An admin with no subscription: nothing counts as news until this moment.
            since = generated_at
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
        if first_pass and send:
            await conn.execute(
                "UPDATE alert_state SET last_watermark = $1 WHERE user_id = $2 "
                "AND last_watermark IS NULL",
                generated_at,
                user["id"],
            )
            log.info("user %s: first pass, baselined at %s, nothing sent", user["id"], generated_at)
            continue
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

        subject = subject_for(alert)
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
