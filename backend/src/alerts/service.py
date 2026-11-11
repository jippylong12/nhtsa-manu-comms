"""Per-user alerts: an email only when there is something to say about the user's cars.

Two kinds of news, one format per car ("we found X bulletins", a serious / medium / small
breakdown, then a preview of at most 1 serious, 2 medium and 3 small):

* Ready. A car's first read finished (`vehicles.ready_at`, set once by a sending pass when
  the car's first sync is done and nothing linked to it is still waiting to be read). Every
  account that was waiting on the car is told once, free or paid. An account that adds a car
  we already read gets no email: it is ready when they add it.
* New. Sync sets `communication_vehicles.alert_pending` on a bulletin it links to a car
  whose first sync is already behind it (a car's history, read for the first time, is never
  flagged). A sending pass mails every flagged, processed bulletin to the accounts that hold
  the car, then clears the flags, so a bulletin is announced once and a day with nothing new
  sends nothing. Re-reading a bulletin later never announces it again.

What an account sees depends on what it may read. Full access (a subscription place, an
unlock, an admin) gets the ready preview in full and every new bulletin in full, with
nothing to click through for. A free account gets its free sample in
full in the ready email, and in the new email only the opening words of each bulletin with
a button to unlock the car. A subscriber's car past the plan's count is left out.

Each recipient has an `alert_state` watermark (the newest moment already mailed). It only
advances after a send succeeds, so when one user's send fails the flags for that user's cars
stay set, the next run repeats exactly their items, and the users who were already mailed
are not mailed twice. An account with no watermark starts from when it subscribed, else
from when it was created; an admin with neither is baselined silently on the first pass.

A dry run (`send=False`) renders one HTML file per user with news and never calls
Resend, moves a watermark, marks a car ready or clears a flag.
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
# How many bulletins of each severity one car shows in an email; the rest are a count and
# a button into the app. The same numbers as the free sample (`SAMPLE_QUOTA` in src/me).
PREVIEW_QUOTA = {"serious": 1, "medium": 2, "small": 3}
UNLOCK_PRICE = "$3"
# A locked bulletin shows this many characters of its summary, cut at a word.
TEASER_CHARS = 110


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
    """One car's section of an email: the bulletins shown, and the totals behind them."""

    label: str
    items: list[AlertItem] = field(default_factory=list)
    # 0 when unknown: the email then leaves out the links into the app for this car.
    vehicle_id: int = 0
    # Every bulletin the section is about, by severity; `items` is only the preview.
    # None means the preview is everything.
    counts: Optional[dict[str, int]] = None
    # The account reads this car as a free sample: no bulletin beyond the sample in full.
    locked: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values()) if self.counts is not None else len(self.items)

    @property
    def breakdown(self) -> dict[str, int]:
        if self.counts is not None:
            return self.counts
        found = dict.fromkeys(SEVERITY_LABELS, 0)
        for it in self.items:
            found[it.severity if it.severity in found else "small"] += 1
        return found


@dataclass
class ReadyVehicle:
    """A car whose first read just finished: the total found and a preview of it."""

    vehicle_id: int
    label: str
    count: int
    counts: Optional[dict[str, int]] = None
    items: list[AlertItem] = field(default_factory=list)
    locked: bool = False


@dataclass
class UserAlert:
    generated_at: datetime
    since: Optional[datetime]
    groups: list[VehicleGroup]
    ready_vehicles: list[ReadyVehicle]
    watermark: Optional[datetime]  # the newest moment this alert covers

    @property
    def total_items(self) -> int:
        """New bulletins across the user's cars (all of them, not only the ones shown)."""
        return sum(g.total for g in self.groups)

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
    """Every account with a car, plus subscribers and admins; minus unsubscribed.

    Creates the user's `alert_state` row (with a fresh token) on first use. Each dict
    carries id, email, is_admin, created_at, unsubscribe_token, last_watermark and
    subscribed_since (when the user's earliest active subscription was created, else None).
    """
    eligible = """
        u.is_admin
        OR EXISTS (SELECT 1 FROM user_vehicles uv WHERE uv.user_id = u.id)
        OR EXISTS (
            SELECT 1 FROM entitlements e
            WHERE e.user_id = u.id AND e.kind = 'subscription' AND e.status = 'active')
    """
    await conn.execute(
        f"""
        INSERT INTO alert_state (user_id)
        SELECT u.id FROM users u WHERE {eligible}
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    rows = await conn.fetch(
        f"""
        SELECT u.id, u.email, u.display_name, u.is_admin, u.created_at,
               a.unsubscribe_token, a.last_watermark,
               (SELECT min(e.created_at) FROM entitlements e
                WHERE e.user_id = u.id AND e.kind = 'subscription' AND e.status = 'active'
               ) AS subscribed_since
        FROM users u
        JOIN alert_state a ON a.user_id = u.id
        WHERE a.unsubscribed_at IS NULL
          AND ($1::bigint IS NULL OR u.id = $1)
          AND ({eligible})
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
    """Where a user's news starts: their watermark, else when they subscribed, else when
    the account was created.

    None means an admin with no watermark and no subscription: the caller baselines them
    at the moment of the pass instead of treating their history as news.
    """
    known = user["last_watermark"] or user.get("subscribed_since")
    if known or user.get("is_admin"):
        return known
    return user.get("created_at")


_BULLETINS_SQL = """
    SELECT
        c.nhtsa_id, c.communication_number, c.communication_type, c.communication_date,
        c.summary, c.severity, c.processed_at,
        -- When this became news for this car: read, and linked to the car. A bulletin
        -- read long ago for one car can be linked to another car today.
        greatest(c.processed_at, cv.first_seen_at) AS news_at,
        v.id AS vehicle_id, v.year, v.make, v.model, v.ready_at,
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
    FROM vehicles v
    JOIN communication_vehicles cv ON cv.vehicle_id = v.id
    JOIN communications c ON c.id = cv.communication_id
    WHERE c.status = 'processed' AND c.processed_at IS NOT NULL
"""


def _fold(rows: list) -> list[tuple]:
    """The current revision of each bulletin family with how many older ones it replaces,
    newest first."""
    by_stem: dict[str, list] = {}
    for r in rows:
        by_stem.setdefault(_stem(r), []).append(r)
    folded = [(max(m, key=_recency), len(m) - 1) for m in by_stem.values()]
    folded.sort(key=lambda t: _recency(t[0]), reverse=True)
    return folded


def _section(
    folded: list[tuple], everything: bool = False
) -> tuple[dict[str, int], list[AlertItem]]:
    """A car's totals by severity and the bulletins to show: the newest PREVIEW_QUOTA of
    each severity, or all of them when `everything`."""
    counts = dict.fromkeys(SEVERITY_LABELS, 0)
    items: list[AlertItem] = []
    for r, superseded in folded:
        severity = r["severity"] if r["severity"] in counts else "small"
        counts[severity] += 1
        if everything or counts[severity] <= PREVIEW_QUOTA[severity]:
            items.append(
                AlertItem(
                    nhtsa_id=r["nhtsa_id"],
                    severity=severity,
                    communication_type=r["communication_type"],
                    communication_date=r["communication_date"],
                    summary=r["summary"] or "",
                    llm_summary=r["llm_summary"],
                    symptoms=list(r["symptoms"]),
                    systems=list(r["systems"]),
                    url=r["url"],
                    superseded_count=superseded,
                )
            )
    return counts, items


async def build_for_user(
    conn, user: dict, since: Optional[datetime], generated_at: datetime
) -> UserAlert:
    """What there is to tell the user since `since`. Pure read.

    `ready_vehicles`: cars whose first read finished after `since`, with everything found.
    `groups`: for the user's other cars, the bulletins flagged as new and processed after
    `since`. A subscriber's car past the plan's count is in neither: it is a sample, and
    its place in the email belongs to the cars the plan covers.
    """
    linked = await conn.fetch(
        "SELECT vehicle_id FROM user_vehicles WHERE user_id = $1 AND alerts_enabled", user["id"]
    )
    locked: dict[int, bool] = {}
    for r in linked:
        full = await access_for(conn, user["id"], r["vehicle_id"]) == "full"
        if full or not user.get("subscribed_since"):
            locked[r["vehicle_id"]] = not full
    vehicle_ids = list(locked)

    ready_rows = await conn.fetch(
        _BULLETINS_SQL
        + " AND v.id = ANY($1::bigint[]) AND v.ready_at IS NOT NULL"
        " AND ($2::timestamptz IS NULL OR v.ready_at > $2)",
        vehicle_ids,
        since,
    )
    ready_ids = {r["vehicle_id"] for r in ready_rows}
    new_rows = await conn.fetch(
        _BULLETINS_SQL
        + " AND v.id = ANY($1::bigint[]) AND cv.alert_pending"
        " AND ($2::timestamptz IS NULL OR greatest(c.processed_at, cv.first_seen_at) > $2)",
        [vid for vid in vehicle_ids if vid not in ready_ids],
        since,
    )

    watermark = since
    by_vehicle: dict[int, list] = {}
    for r in [*ready_rows, *new_rows]:
        by_vehicle.setdefault(r["vehicle_id"], []).append(r)
        seen = r["ready_at"] if r["vehicle_id"] in ready_ids else r["news_at"]
        if watermark is None or seen > watermark:
            watermark = seen

    groups: list[VehicleGroup] = []
    ready: list[ReadyVehicle] = []
    for vid, rows in by_vehicle.items():
        label = display_vehicle(rows[0]["year"], rows[0]["make"], rows[0]["model"])
        # New bulletins on a car the account may read are shown whole, every one of them:
        # a paying reader never has to click through for news. A ready car (its whole
        # history) and a locked car keep the short preview.
        is_ready = vid in ready_ids
        counts, items = _section(_fold(rows), everything=not is_ready and not locked[vid])
        if is_ready:
            ready.append(
                ReadyVehicle(
                    vehicle_id=vid,
                    label=label,
                    count=sum(counts.values()),
                    counts=counts,
                    items=items,
                    locked=locked[vid],
                )
            )
        else:
            groups.append(
                VehicleGroup(
                    label=label, vehicle_id=vid, items=items, counts=counts, locked=locked[vid]
                )
            )
    groups.sort(key=lambda g: g.label)
    ready.sort(key=lambda r: r.label)

    return UserAlert(
        generated_at=generated_at,
        since=since,
        groups=groups,
        ready_vehicles=ready,
        watermark=watermark,
    )


async def mark_ready_vehicles(conn, generated_at: datetime) -> int:
    """Stamp `ready_at` on every car whose first read just finished. Returns how many.

    Finished means the first sync is done, at least one bulletin was read and none linked
    to the car is still waiting, so the total an email quotes is the whole of it.
    """
    result = await conn.execute(
        """
        UPDATE vehicles v SET ready_at = $1
        WHERE v.ready_at IS NULL AND v.first_synced_at IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM communication_vehicles cv
              JOIN communications c ON c.id = cv.communication_id
              WHERE cv.vehicle_id = v.id AND c.status = 'processed')
          AND NOT EXISTS (
              SELECT 1 FROM communication_vehicles cv
              JOIN communications c ON c.id = cv.communication_id
              WHERE cv.vehicle_id = v.id AND c.status = 'pending')
        """,
        generated_at,
    )
    return int(result.split()[-1])


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
    return f"You get this because you have a car saved at {_base_url()}."


def _opening(text: str) -> str:
    """The first words of a summary, for a bulletin the account has not unlocked."""
    text = " ".join((text or "").split())
    if len(text) <= TEASER_CHARS:
        return text
    return text[:TEASER_CHARS].rsplit(" ", 1)[0].rstrip(",;:.") + "\u2026"


@dataclass
class _Section:
    """One car as the email shows it, whichever kind of news it is."""

    label: str
    vehicle_id: int
    ready: bool
    total: int
    counts: dict[str, int]
    items: list[AlertItem]
    locked: bool

    @property
    def found(self) -> str:
        if self.ready:
            return f"We found {_plural(self.total, 'bulletin')}"
        return f"We found {_plural(self.total, 'new bulletin')}"

    @property
    def teaser(self) -> bool:
        """New bulletins on a car the account has not unlocked: opening words only."""
        return self.locked and not self.ready

    @property
    def more(self) -> int:
        return self.total - len(self.items)


def _sections(alert: UserAlert) -> list[_Section]:
    ready = [
        _Section(
            label=rv.label,
            vehicle_id=rv.vehicle_id,
            ready=True,
            total=rv.count,
            counts=rv.counts or {},
            items=rv.items,
            locked=rv.locked,
        )
        for rv in alert.ready_vehicles
    ]
    new = [
        _Section(
            label=g.label,
            vehicle_id=g.vehicle_id,
            ready=False,
            total=g.total,
            counts=g.breakdown,
            items=g.items,
            locked=g.locked,
        )
        for g in alert.groups
    ]
    return ready + new


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
    for sec in _sections(alert):
        car_url = f"{base}/app/vehicles/{sec.vehicle_id}" if sec.vehicle_id else ""
        lines.append(f"== {sec.label}{' (now ready)' if sec.ready else ''} ==")
        split = ", ".join(
            f"{n} {SEVERITY_LABELS[sev].lower()}" for sev, n in sec.counts.items() if n
        )
        lines.append(f"{sec.found}{': ' + split if split else ''}.")
        lines.append("")
        for it in sec.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            label, _ = _severity(it)
            lines.append(f"- [{label}] {date}{_revision_note(it)}")
            if sec.teaser:
                lines.append(f"  {_opening(it.llm_summary or it.summary)}")
                lines.append("")
                continue
            lines.append(f"  {it.llm_summary or it.summary}")
            if it.systems:
                lines.append(f"  Systems: {', '.join(it.systems)}")
            if it.symptoms:
                lines.append(f"  Symptoms: {', '.join(it.symptoms[:5])}")
            if sec.vehicle_id:
                lines.append(f"  See if your car has this: {_item_url(base, sec.vehicle_id, it)}")
            if it.url:
                lines.append(f"  Original document: {it.url}")
            lines.append("")
        if car_url and sec.locked:
            lines.append(f"Unlock this car for {UNLOCK_PRICE}: {car_url}")
            lines.append("")
        elif car_url and sec.more > 0:
            lines.append(f"See all {sec.total} in the app: {car_url}")
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


def _card_html(base: str, sec: _Section, it: AlertItem) -> str:
    """One bulletin: in full, or its opening words when the car is not unlocked."""
    date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
    label, color = _severity(it)
    if sec.teaser:
        body = escape(_opening(it.llm_summary or it.summary) or "No summary available")
        extras = ""
        text_color = "#aab4c5"
    else:
        body = escape(it.llm_summary or it.summary or "No summary available")
        text_color = "#e8edf5"
        chips = "".join(
            f'<span style="display:inline-block;font-size:12px;color:#aac4f5;'
            f"background:#1a2740;border-radius:10px;padding:3px 9px;"
            f'margin:0 6px 6px 0;">{escape(s)}</span>'
            for s in it.systems[:4]
        )
        actions = []
        if sec.vehicle_id:
            actions.append(_button(_item_url(base, sec.vehicle_id, it), "See if your car has this"))
        if _safe_link(it.url):
            actions.append(
                f'<a href="{escape(it.url)}" style="display:inline-block;color:#8fb4ff;'
                f"font-size:13px;text-decoration:none;padding:10px 0 10px "
                f'{14 if actions else 0}px;">Original document (PDF)</a>'
            )
        extras = (
            f'<div style="margin-top:12px;">{chips}</div>'
            f'<div style="margin-top:6px;">{"".join(actions)}</div>'
        )
    return f"""
        <tr><td style="padding:0 0 12px;">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="background:#141c2e;border:1px solid #243049;
                      border-left:4px solid {color};border-radius:12px;">
        <tr><td style="padding:16px 18px;">
            <div style="font-size:13px;color:#9ca3af;margin-bottom:8px;">
                <strong style="color:{color};">{label}</strong>
                &nbsp;&middot;&nbsp;{date}{escape(_revision_note(it))}
            </div>
            <div style="font-size:16px;color:{text_color};line-height:1.55;">
                {body}
            </div>
            {extras}
        </td></tr></table>
        </td></tr>
        """


def _breakdown_html(counts: dict[str, int]) -> str:
    """'2 serious · 5 medium · 9 small', each count in its severity colour."""
    parts = [
        f'<span style="white-space:nowrap;"><strong style="color:{SEVERITY_COLORS[sev]};">'
        f"{n}</strong> {SEVERITY_LABELS[sev].lower()}</span>"
        for sev, n in counts.items()
        if n and sev in SEVERITY_LABELS
    ]
    return " &nbsp;&middot;&nbsp; ".join(parts)


def _section_html(base: str, sec: _Section) -> str:
    car_url = f"{base}/app/vehicles/{sec.vehicle_id}" if sec.vehicle_id else ""
    badge = (
        '<span style="display:inline-block;font-size:11px;font-weight:700;'
        "text-transform:uppercase;letter-spacing:0.06em;color:#34c777;background:#10231b;"
        'border:1px solid #1f5137;border-radius:10px;padding:2px 8px;margin-left:8px;'
        'vertical-align:middle;">Now ready</span>'
        if sec.ready
        else ""
    )
    open_car = (
        f'<a href="{escape(car_url)}" style="color:#8fb4ff;font-size:13px;'
        f'text-decoration:none;font-weight:600;">Open this car &rarr;</a>'
        if car_url
        else ""
    )
    cards = "".join(_card_html(base, sec, it) for it in sec.items)

    footer = ""
    if car_url and sec.locked:
        what = (
            f"Your free sample is above. Unlock all {sec.total} for this car, forever."
            if sec.ready
            else f"Unlock this car to read {'it' if sec.total == 1 else 'these'} in full "
            "and see whether yours is affected."
        )
        footer = (
            f'<div style="font-size:14px;line-height:1.5;color:#c7d0de;margin-bottom:12px;">'
            f"{escape(what)}</div>"
            f"{_button(car_url, f'Unlock now for {UNLOCK_PRICE}')}"
            f'<div style="font-size:12px;color:#8a94a6;margin-top:10px;">'
            f"Or subscribe to open every car on your account.</div>"
        )
    elif car_url and sec.more > 0:
        footer = _button(car_url, f"See all {sec.total} in the app")
    if footer:
        footer = (
            '<tr><td style="padding:0 0 4px;"><table width="100%" cellpadding="0" '
            'cellspacing="0" role="presentation" style="background:#101726;'
            'border:1px dashed #2b3a57;border-radius:12px;"><tr>'
            f'<td style="padding:16px 18px;">{footer}</td></tr></table></td></tr>'
        )

    return f"""
        <tr><td style="padding:28px 0 12px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>
            <td style="font-size:19px;font-weight:700;color:#f8fafc;">
                {escape(sec.label)}{badge}</td>
            <td align="right" style="white-space:nowrap;">{open_car}</td>
            </tr></table>
            <div style="font-size:15px;color:#e5e7eb;margin-top:8px;">
                <strong style="color:#f8fafc;">{escape(sec.found)}</strong>
                <span style="color:#9ca3af;">&nbsp;&nbsp;{_breakdown_html(sec.counts)}</span>
            </div>
        </td></tr>
        {cards}
        {footer}
        """


def render_html(alert: UserAlert, unsubscribe_url: str) -> str:
    """The alert email. Inline styles and tables only: email clients strip <style> and SVG."""
    base = _base_url()
    sections = [_section_html(base, sec) for sec in _sections(alert)]

    n = alert.total_items
    if n:
        whose = "car" if len(alert.groups) == 1 else "cars"
        headline = f"{_plural(n, 'new bulletin')} for your {whose}"
        lede = (
            "Your carmaker sent these to its dealers. Here is what each one means for you."
        )
    elif len(alert.ready_vehicles) == 1:
        headline = f"Your {alert.ready_vehicles[0].label} is ready to read"
        lede = "We have finished reading its bulletins."
    elif alert.ready_vehicles:
        headline, lede = "Your cars are ready to read", "We have finished reading their bulletins."
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
    # A full sending pass announces every flagged bulletin that is ready to read, then
    # clears those flags. A pass for one user leaves them for everyone else.
    clearing = send and only_user_id is None
    if send:
        marked = await mark_ready_vehicles(conn, generated_at)
        if marked:
            log.info("%d car(s) finished their first read", marked)
    announced = (
        await conn.fetch(
            """
            SELECT cv.communication_id, cv.vehicle_id
            FROM communication_vehicles cv
            JOIN communications c ON c.id = cv.communication_id
            WHERE cv.alert_pending AND c.status = 'processed' AND c.processed_at IS NOT NULL
            """
        )
        if clearing
        else []
    )
    failed_vehicle_ids: set[int] = set()
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
            failed_vehicle_ids.update(g.vehicle_id for g in alert.groups)
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
        log.info(
            "user %s: sent (%d items), message id %s",
            user["id"],
            alert.total_items,
            outcome.message_id,
        )

    # Mark what was announced. A car with a failed send keeps its flags so that user gets
    # the same items next run; everyone already mailed is held back by their watermark.
    done = [r for r in announced if r["vehicle_id"] not in failed_vehicle_ids]
    if done:
        await conn.execute(
            """
            UPDATE communication_vehicles cv SET alert_pending = false
            FROM unnest($1::bigint[], $2::bigint[]) AS t(communication_id, vehicle_id)
            WHERE cv.communication_id = t.communication_id AND cv.vehicle_id = t.vehicle_id
            """,
            [r["communication_id"] for r in done],
            [r["vehicle_id"] for r in done],
        )
        log.info("cleared the new flag on %d bulletin link(s)", len(done))
    return summary
