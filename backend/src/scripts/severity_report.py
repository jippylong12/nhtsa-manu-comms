"""Severity report for the schema-v2 re-extraction (JIP-1580).

Read-only (SELECTs only), so it is safe against the replica. Prints Markdown:
severity by source over all communications; for the MC bulletins the tier, doc_kind
and audience distributions plus random examples per tier with the classifier reason
(recomputed with ``classify_from_document``, the same call promotion makes); the
ledger's input-plan mix and spend for project ``schema-v2``; and the communications
still pending or failed with their reasons.

Usage:
    cd backend && set -a && source .env && set +a && \
        DATABASE_URL="$TEST_DATABASE_URL" python -m src.scripts.severity_report
    ... python -m src.scripts.severity_report --json
"""

import argparse
import asyncio
import json
import logging
from typing import Any

import asyncpg

from src.communications.severity import classify_from_document
from src.db import close_pool, get_pool
from src.jobs.llm import PROJECT

TIERS = ("serious", "medium", "small")
EXAMPLES_PER_TIER = 10


def _cell(value: Any, limit: int | None = None) -> str:
    """A table cell: None as a dash, pipes and newlines made safe, optionally truncated."""
    text = "-" if value is None else str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text[:limit] if limit else text


def _table(head: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    if not rows:
        return "_none_"
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


async def collect(conn: asyncpg.Connection, per_tier: int = EXAMPLES_PER_TIER) -> dict[str, Any]:
    """Read everything the report shows. SELECTs only."""
    data: dict[str, Any] = {}
    data["severity_by_source"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT severity, severity_source, count(*) AS n FROM communications "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )
    ]
    mc = "communication_type = 'MC'"
    data["mc_total"] = await conn.fetchval(f"SELECT count(*) FROM communications WHERE {mc}")
    data["mc_by_severity"] = [
        dict(r)
        for r in await conn.fetch(
            f"SELECT severity, count(*) AS n FROM communications WHERE {mc} GROUP BY 1 ORDER BY 1"
        )
    ]
    # The first document per communication, the one promotion classifies from.
    first_doc = (
        "LEFT JOIN LATERAL (SELECT d.doc_kind, d.audience, d.symptoms, d.remedy "
        "FROM comm_documents d WHERE d.communication_id = c.id ORDER BY d.id LIMIT 1) d ON true"
    )
    data["mc_by_doc_kind"] = [
        dict(r)
        for r in await conn.fetch(
            f"SELECT d.doc_kind, count(*) AS n FROM communications c {first_doc} "
            f"WHERE c.{mc} GROUP BY 1 ORDER BY 2 DESC"
        )
    ]
    data["mc_by_audience"] = [
        dict(r)
        for r in await conn.fetch(
            f"SELECT d.audience, count(*) AS n FROM communications c {first_doc} "
            f"WHERE c.{mc} GROUP BY 1 ORDER BY 2 DESC"
        )
    ]
    examples: dict[str, list[dict[str, Any]]] = {}
    for tier in TIERS:
        rows = await conn.fetch(
            "SELECT c.nhtsa_id, c.communication_number, left(c.summary, 120) AS summary, "
            "d.doc_kind, d.audience, d.symptoms, d.remedy "
            f"FROM communications c {first_doc} "
            f"WHERE c.{mc} AND c.severity = $1 ORDER BY random() LIMIT $2",
            tier,
            per_tier,
        )
        out = []
        for r in rows:
            _, reason = classify_from_document(r["doc_kind"], r["symptoms"], r["remedy"], r["audience"])
            out.append(
                {
                    "nhtsa_id": r["nhtsa_id"],
                    "communication_number": r["communication_number"],
                    "summary": r["summary"],
                    "doc_kind": r["doc_kind"],
                    "audience": r["audience"],
                    "reason": reason,
                }
            )
        examples[tier] = out
    data["mc_examples"] = examples
    data["ledger"] = [
        {"model": r["model"], "input": r["input"], "n": r["n"], "cost_usd": float(r["cost"])}
        for r in await conn.fetch(
            "SELECT model, meta->>'input' AS input, count(*) AS n, "
            "coalesce(sum(cost_usd), 0) AS cost FROM usage_events "
            "WHERE meta->>'project' = $1 GROUP BY 1, 2 ORDER BY 1, 2",
            PROJECT,
        )
    ]
    data["unfinished"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT status, status_reason, count(*) AS n FROM communications "
            "WHERE status IN ('pending', 'failed') GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2"
        )
    ]
    return data


def render(data: dict[str, Any]) -> str:
    """Pure: the collected data as Markdown."""
    parts = ["# Severity report", "", "## Severity x severity_source (all communications)", ""]
    parts.append(
        _table(
            ("severity", "severity_source", "count"),
            [(r["severity"], r["severity_source"], r["n"]) for r in data["severity_by_source"]],
        )
    )
    parts += ["", f"## MC bulletins ({data['mc_total']})", "", "### Per severity", ""]
    parts.append(_table(("severity", "count"), [(r["severity"], r["n"]) for r in data["mc_by_severity"]]))
    parts += ["", "### Per doc_kind", ""]
    parts.append(_table(("doc_kind", "count"), [(r["doc_kind"], r["n"]) for r in data["mc_by_doc_kind"]]))
    parts += ["", "### Per audience", ""]
    parts.append(_table(("audience", "count"), [(r["audience"], r["n"]) for r in data["mc_by_audience"]]))
    for tier in TIERS:
        rows = data["mc_examples"][tier]
        parts += ["", f"## MC examples: {tier}", ""]
        parts.append(
            _table(
                ("nhtsa_id", "communication_number", "summary", "doc_kind", "audience", "reason"),
                [
                    (
                        r["nhtsa_id"],
                        r["communication_number"],
                        r["summary"],
                        r["doc_kind"],
                        r["audience"],
                        r["reason"],
                    )
                    for r in rows
                ],
            )
        )
    parts += ["", f"## Ledger: project {PROJECT}", ""]
    parts.append(
        _table(
            ("model", "input", "calls", "cost_usd"),
            [(r["model"], r["input"], r["n"], f"{r['cost_usd']:.4f}") for r in data["ledger"]],
        )
    )
    total = sum(r["cost_usd"] for r in data["ledger"])
    parts += ["", f"Total spend: ${total:.4f}", "", "## Still pending or failed", ""]
    parts.append(
        _table(
            ("status", "status_reason", "count"),
            [(r["status"], r["status_reason"], r["n"]) for r in data["unfinished"]],
        )
    )
    return "\n".join(parts) + "\n"


async def build_report(conn: asyncpg.Connection, per_tier: int = EXAMPLES_PER_TIER) -> str:
    """The report as a Markdown string."""
    return render(await collect(conn, per_tier))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Severity report for the schema-v2 re-extraction.")
    parser.add_argument("--json", action="store_true", help="emit the collected data as JSON")
    parser.add_argument("--per-tier", type=int, default=EXAMPLES_PER_TIER, help="examples per tier")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    async def _main() -> int:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                data = await collect(conn, args.per_tier)
            print(json.dumps(data, indent=2, default=str) if args.json else render(data))
            return 0
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
