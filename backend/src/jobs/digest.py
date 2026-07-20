"""Email digest of newly processed communications.

After a processor run, collect communications processed since the last digest
watermark, grouped by vehicle, and email a summary. This is the Phase A
prototype of Phase C's alerting product.

Watermark discipline: the watermark only advances after a send (or a dry-run
render) actually succeeds, and the query is `processed_at > watermark`. So a
failed run leaves the watermark untouched and the next run picks up exactly the
same items plus any new ones, never duplicating and never skipping.

Sending is off by default: `build_digest()` and `render_*()` never touch the
network, and `run_digest(send=False)` writes the HTML to disk for review. A real
send requires `send=True` plus `resend_api_key`, `digest_from_email`, and
`digest_to_email` in the environment.

Usage:
    python -m src.jobs.digest                 # dry run: render HTML to disk
    python -m src.jobs.digest --send          # actually send + advance watermark
    python -m src.jobs.digest --since-days 30 # override watermark for a test render
"""

import argparse
import asyncio
import logging
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from src.config import get_settings
from src.db import close_pool, get_pool

log = logging.getLogger("digest")


@dataclass
class DigestItem:
    nhtsa_id: str
    communication_type: Optional[str]
    communication_date: Optional[datetime]
    summary: str
    llm_summary: Optional[str]
    symptoms: list[str]
    systems: list[str]
    url: Optional[str]


@dataclass
class VehicleGroup:
    label: str
    items: list[DigestItem] = field(default_factory=list)


@dataclass
class Digest:
    generated_at: datetime
    since: Optional[datetime]
    groups: list[VehicleGroup]
    watermark: Optional[datetime]  # max processed_at in this batch

    @property
    def total_items(self) -> int:
        return sum(len(g.items) for g in self.groups)

    @property
    def has_news(self) -> bool:
        return self.total_items > 0


async def _get_watermark(conn) -> Optional[datetime]:
    return await conn.fetchval("SELECT last_watermark FROM digest_state WHERE id = true")


async def build_digest(since: Optional[datetime], generated_at: datetime) -> Digest:
    """Collect communications processed since `since`, grouped by vehicle.

    Pure read. `generated_at` is passed in rather than read from the clock so
    the caller controls it (and tests stay deterministic).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.nhtsa_id, c.communication_type, c.communication_date,
                c.summary, c.processed_at,
                v.nhtsa_vehicle_id, v.year, v.make, v.model,
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
            FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            JOIN vehicles v ON v.id = cv.vehicle_id
            WHERE c.status = 'processed'
              AND c.processed_at IS NOT NULL
              AND ($1::timestamptz IS NULL OR c.processed_at > $1)
            ORDER BY v.year, v.model, c.communication_date DESC NULLS LAST
            """,
            since,
        )

    groups: dict[str, VehicleGroup] = {}
    watermark = since
    for r in rows:
        label = f"{r['year']} {r['make']} {r['model']}".strip()
        grp = groups.setdefault(label, VehicleGroup(label=label))
        grp.items.append(
            DigestItem(
                nhtsa_id=r["nhtsa_id"],
                communication_type=r["communication_type"],
                communication_date=r["communication_date"],
                summary=r["summary"] or "",
                llm_summary=r["llm_summary"],
                symptoms=list(r["symptoms"]),
                systems=list(r["systems"]),
                url=r["url"],
            )
        )
        if r["processed_at"] and (watermark is None or r["processed_at"] > watermark):
            watermark = r["processed_at"]

    return Digest(
        generated_at=generated_at,
        since=since,
        groups=list(groups.values()),
        watermark=watermark,
    )


def _safe_link(url: Optional[str]) -> bool:
    """True only for http(s) URLs.

    `html.escape` neutralizes quote breakout but not the scheme, so a stored
    `javascript:`/`data:` URL would still render as a live link (dangerous when
    the dry-run preview HTML is opened in a browser). Document URLs come from
    the NHTSA API, but they are external data, so allowlist the scheme.
    """
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def render_text(digest: Digest) -> str:
    """Plain-text alternative part."""
    lines = [
        f"NHTSA Comms digest - {digest.generated_at:%b %d, %Y}",
        f"{digest.total_items} new communication(s) for your vehicles.",
        "",
    ]
    for grp in digest.groups:
        lines.append(f"== {grp.label} ({len(grp.items)}) ==")
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
            lines.append(f"- [{it.communication_type or 'OTHER'}] {date}")
            lines.append(f"  {it.llm_summary or it.summary}")
            if it.systems:
                lines.append(f"  Systems: {', '.join(it.systems)}")
            if it.symptoms:
                lines.append(f"  Symptoms: {', '.join(it.symptoms[:5])}")
            if it.url:
                lines.append(f"  {it.url}")
            lines.append("")
    return "\n".join(lines)


def render_html(digest: Digest) -> str:
    """Clean, dark-mode-friendly HTML. Inline styles only (email clients strip
    <style> and external CSS)."""
    items_html = []
    for grp in digest.groups:
        rows = []
        for it in grp.items:
            date = f"{it.communication_date:%b %d, %Y}" if it.communication_date else "Unknown date"
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
                        <strong style="color:#f59e0b;">{escape(it.communication_type or 'OTHER')}</strong>
                        &nbsp;&middot;&nbsp;{date}
                    </div>
                    <div style="font-size:15px;color:#e5e7eb;line-height:1.5;margin-bottom:6px;">
                        {escape(it.llm_summary or it.summary or 'No summary available')}
                    </div>
                    <div>{chips}</div>
                    <div style="margin-top:6px;">{link}</div>
                </td></tr>
                """
            )
        items_html.append(
            f"""
            <tr><td style="padding-top:24px;">
                <div style="font-size:13px;font-weight:600;text-transform:uppercase;
                     letter-spacing:0.05em;color:#60a5fa;">{escape(grp.label)}
                     <span style="color:#6b7280;">({len(grp.items)})</span></div>
                <table width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
            </td></tr>
            """
        )

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0f17;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f17;">
<tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;
     font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <tr><td>
        <div style="font-size:22px;font-weight:700;color:#f9fafb;">NHTSA Comms digest</div>
        <div style="font-size:14px;color:#9ca3af;margin-top:4px;">
            {digest.generated_at:%B %d, %Y} &middot;
            {digest.total_items} new communication{'s' if digest.total_items != 1 else ''}
            for your vehicles
        </div>
    </td></tr>
    {''.join(items_html)}
    <tr><td style="padding-top:32px;font-size:12px;color:#6b7280;
         border-top:1px solid #1f2937;margin-top:24px;">
        Generated by the NHTSA Comms pipeline. Communications are sourced from NHTSA
        and summarized automatically; verify against the source document before acting.
    </td></tr>
</table>
</td></tr></table>
</body></html>"""


async def _advance_watermark(watermark: datetime, sent_at: datetime) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE digest_state SET last_watermark = $1, last_sent_at = $2 WHERE id = true",
            watermark,
            sent_at,
        )


def _send_email(subject: str, html: str, text: str) -> str:
    """Send via Resend. Returns the message id. Raises on misconfig or API error."""
    import resend

    settings = get_settings()
    missing = [
        name
        for name, val in (
            ("RESEND_API_KEY", settings.resend_api_key),
            ("DIGEST_FROM_EMAIL", settings.digest_from_email),
            ("DIGEST_TO_EMAIL", settings.digest_to_email),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"cannot send: missing {', '.join(missing)} in the environment")

    resend.api_key = settings.resend_api_key
    result = resend.Emails.send(
        {
            "from": settings.digest_from_email,
            "to": [settings.digest_to_email],
            "subject": subject,
            "html": html,
            "text": text,
        }
    )
    return result.get("id", "") if isinstance(result, dict) else str(result)


async def run_digest(
    send: bool,
    generated_at: datetime,
    since_override: Optional[datetime] = None,
    out_dir: Optional[pathlib.Path] = None,
) -> Digest:
    """Build and either render (dry run) or send the digest.

    `generated_at` is injected by the caller so the module never calls the clock
    directly (keeps it testable and resume-safe).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        since = since_override if since_override is not None else await _get_watermark(conn)

    digest = await build_digest(since, generated_at)

    if not digest.has_news:
        # No-news default: send nothing.
        log.info("no new communications since %s; sending nothing", since)
        return digest

    subject = f"NHTSA Comms: {digest.total_items} new for your vehicles"
    html = render_html(digest)
    text = render_text(digest)

    if not send:
        out = (out_dir or pathlib.Path(".")) / "digest_preview.html"
        out.write_text(html)
        log.info(
            "DRY RUN: %d item(s) across %d vehicle(s); preview written to %s",
            digest.total_items,
            len(digest.groups),
            out,
        )
        return digest

    message_id = await asyncio.to_thread(_send_email, subject, html, text)
    log.info("sent digest (%d items), message id %s", digest.total_items, message_id)
    if digest.watermark:
        await _advance_watermark(digest.watermark, generated_at)
        log.info("watermark advanced to %s", digest.watermark)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Email digest of new communications.")
    parser.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    parser.add_argument(
        "--since-days", type=int, help="ignore the watermark; include the last N days"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --since-days is a test-render knob. Combining it with --send would email
    # an arbitrary window as "new" and then rewrite the real watermark to that
    # window's max, corrupting it. Refuse the combination.
    if args.since_days is not None and args.send:
        parser.error("--since-days is for dry-run rendering only; do not combine it with --send")

    async def _main() -> int:
        now = datetime.now(timezone.utc)
        since_override = (
            now - timedelta(days=args.since_days) if args.since_days is not None else None
        )
        try:
            await run_digest(send=args.send, generated_at=now, since_override=since_override)
            return 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
