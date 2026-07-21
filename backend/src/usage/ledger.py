"""The one way to record billable usage.

Every path that spends money on a model call goes through ``record_usage`` so no
call site can quietly skip metering. Cost is always derived here from
``usage.pricing``, so a caller cannot record a figure computed from a stale rate.

Recording is best-effort by design: a ledger write must never take down a user's
search or abort a long pipeline run. Failures are logged loudly instead.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.db import get_pool
from src.usage.pricing import cost_of

log = logging.getLogger("usage.ledger")

# Billable operations. Keep in sync with the CHECK constraint in
# migrations/0003_usage_events.sql.
KINDS = (
    "extract",
    "llm_extract",
    "embed",
    "search_embed",
    "answer_compose",
    "digest",
)


async def record_usage(
    kind: str,
    model: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    batch: bool = False,
    unit_count: int = 1,
    communication_id: Optional[int] = None,
    document_id: Optional[int] = None,
    query_id: Optional[str] = None,
    user_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> float:
    """Write one ledger row and return the cost it recorded.

    Never raises: a failed ledger write is logged, not propagated, so metering
    can never break the operation being metered.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown usage kind {kind!r}; expected one of {KINDS}")

    cost = cost_of(model, tokens_in, tokens_out, batch=batch)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO usage_events (
                    kind, model, tokens_in, tokens_out, cost_usd, unit_count,
                    communication_id, document_id, query_id, user_id, meta
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                """,
                kind,
                model,
                tokens_in,
                tokens_out,
                cost,
                unit_count,
                communication_id,
                document_id,
                query_id,
                user_id,
                json.dumps(meta or {}),
            )
    except Exception as e:  # noqa: BLE001
        log.error(
            "failed to record usage (kind=%s model=%s tokens=%s/%s cost=%.10f): %s",
            kind,
            model,
            tokens_in,
            tokens_out,
            cost,
            e,
        )
    return cost


@dataclass
class UsageTotals:
    events: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


def _row_to_totals(row) -> UsageTotals:
    return UsageTotals(
        events=row["events"] or 0,
        tokens_in=row["tokens_in"] or 0,
        tokens_out=row["tokens_out"] or 0,
        cost_usd=float(row["cost_usd"] or 0),
    )


_RANGE = (
    "WHERE ($1::timestamptz IS NULL OR occurred_at >= $1) "
    "AND ($2::timestamptz IS NULL OR occurred_at < $2)"
)

_AGG = (
    "count(*) AS events, coalesce(sum(tokens_in),0) AS tokens_in, "
    "coalesce(sum(tokens_out),0) AS tokens_out, coalesce(sum(cost_usd),0) AS cost_usd"
)


async def summarise(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Totals plus breakdowns by kind, model, and day over a date range.

    The breakdowns are computed from the same rows as the totals, so they
    reconcile by construction.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            f"SELECT {_AGG} FROM usage_events {_RANGE}", date_from, date_to
        )
        by_kind = await conn.fetch(
            f"SELECT kind, {_AGG} FROM usage_events {_RANGE} "
            "GROUP BY kind ORDER BY sum(cost_usd) DESC",
            date_from,
            date_to,
        )
        by_model = await conn.fetch(
            f"SELECT coalesce(model,'(unknown)') AS model, {_AGG} FROM usage_events {_RANGE} "
            "GROUP BY 1 ORDER BY sum(cost_usd) DESC",
            date_from,
            date_to,
        )
        # Deliberately uncapped: a LIMIT here would silently stop the daily
        # breakdown summing to the totals above, which is the one property this
        # endpoint promises. The series is one row per day with spend, and
        # callers bound it with date_from/date_to.
        by_day = await conn.fetch(
            f"SELECT date_trunc('day', occurred_at) AS day, {_AGG} FROM usage_events {_RANGE} "
            "GROUP BY 1 ORDER BY 1 DESC",
            date_from,
            date_to,
        )

    def _grouped(rows, key: str) -> list[dict]:
        out = []
        for r in rows:
            t = _row_to_totals(r)
            out.append(
                {
                    key: r[key],
                    "events": t.events,
                    "tokens_in": t.tokens_in,
                    "tokens_out": t.tokens_out,
                    "cost_usd": round(t.cost_usd, 10),
                }
            )
        return out

    t = _row_to_totals(totals)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "totals": {
            "events": t.events,
            "tokens_in": t.tokens_in,
            "tokens_out": t.tokens_out,
            "cost_usd": round(t.cost_usd, 10),
        },
        "by_kind": _grouped(by_kind, "kind"),
        "by_model": _grouped(by_model, "model"),
        "by_day": _grouped(by_day, "day"),
    }
