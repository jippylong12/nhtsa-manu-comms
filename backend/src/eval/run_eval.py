"""Golden-set retrieval eval harness.

Run from the backend/ directory:

    python -m src.eval.run_eval                 # human-readable report
    python -m src.eval.run_eval --json          # machine-readable
    python -m src.eval.run_eval --k 10          # retrieval depth for MRR

Reports hit@1 / hit@3 / hit@5 and MRR over the committed golden set, plus the
total query-embedding cost of the run. A query counts as a hit at k when any of
its `expected` nhtsa_ids appears in the top-k retrieved communications; MRR uses
the rank of the first expected id. `no_match` queries are excluded from hit@k
(they measure refusal, not retrieval) and reported separately.

Before scoring, every expected id is checked to be linked to its query's
vehicle. A golden label that points at a communication the vehicle does not have
is a fixture bug, and the harness fails loudly rather than silently scoring it 0.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.db import close_pool, get_pool
from src.symptom_search.cost import QueryCost
from src.symptom_search.retrieval import retrieve, revision_stem

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
HIT_KS = (1, 3, 5)


def matches_expected(
    nhtsa_id: str,
    number: Optional[str],
    expected_ids: set[str],
    expected_stems: set[str],
) -> bool:
    """Whether a retrieved communication answers the query.

    Scored at bulletin-family level, not exact nhtsa_id: a family (PIT6304B ->
    PIT6304D) is one bulletin republished, and retrieval deliberately returns its
    current revision. Pinning the historical id the golden set happened to record
    would penalise returning the newer, correct revision.
    """
    if nhtsa_id in expected_ids:
        return True
    stem = revision_stem(number)
    return bool(stem and stem in expected_stems)


@dataclass
class QueryResult:
    id: str
    vehicle_id: int
    query: str
    expected: list[str]
    retrieved: list[str]
    hit_rank: int | None  # 1-based rank of the first expected id, or None
    no_match: bool = False


@dataclass
class EvalReport:
    results: list[QueryResult] = field(default_factory=list)
    # Accumulated across every query. QueryCost is a single-query value object,
    # so the harness sums per-query costs here rather than reusing one instance
    # (whose record_embed would otherwise overwrite, leaving only the last query).
    total_embed_tokens: int = 0
    total_embed_cost_usd: float = 0.0

    @property
    def scored(self) -> list[QueryResult]:
        return [r for r in self.results if not r.no_match]

    def hit_at(self, k: int) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        hits = sum(1 for r in scored if r.hit_rank is not None and r.hit_rank <= k)
        return hits / len(scored)

    def mrr(self) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        return sum((1.0 / r.hit_rank) if r.hit_rank else 0.0 for r in scored) / len(scored)

    def metrics(self) -> dict:
        return {
            "queries_scored": len(self.scored),
            "queries_no_match": len(self.results) - len(self.scored),
            **{f"hit@{k}": round(self.hit_at(k), 4) for k in HIT_KS},
            "mrr": round(self.mrr(), 4),
            "embed_tokens_total": self.total_embed_tokens,
            "embed_cost_usd_total": round(self.total_embed_cost_usd, 8),
            "cost_per_query_usd": (
                round(self.total_embed_cost_usd / len(self.results), 8) if self.results else 0.0
            ),
        }


def load_golden(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["queries"]


async def _preflight(conn, queries: list[dict]) -> list[str]:
    """Return a list of fixture problems: expected ids not linked to their vehicle."""
    problems: list[str] = []
    for q in queries:
        if q.get("no_match"):
            continue
        for nid in q["expected"]:
            linked = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM communications c
                    JOIN communication_vehicles cv ON cv.communication_id = c.id
                    JOIN vehicles v ON v.id = cv.vehicle_id
                    WHERE c.nhtsa_id = $1 AND v.id = $2
                )
                """,
                nid,
                q["vehicle_id"],
            )
            if not linked:
                problems.append(
                    f"query {q['id']!r}: expected {nid} is not linked to vehicle {q['vehicle_id']}"
                )
    return problems


async def _expected_stems(conn, queries: list[dict]) -> dict[str, set[str]]:
    """Query id -> the bulletin-family stems of its expected communications."""
    out: dict[str, set[str]] = {}
    for q in queries:
        ids = q.get("expected") or []
        if not ids:
            out[q["id"]] = set()
            continue
        rows = await conn.fetch(
            "SELECT communication_number FROM communications WHERE nhtsa_id = ANY($1::text[])",
            list(ids),
        )
        stems = {revision_stem(r["communication_number"]) for r in rows}
        out[q["id"]] = {s for s in stems if s}
    return out


async def evaluate(golden_path: Path = GOLDEN_PATH, retrieve_k: int = 10) -> EvalReport:
    queries = load_golden(golden_path)

    pool = await get_pool()
    async with pool.acquire() as conn:
        problems = await _preflight(conn, queries)
        stems_by_query = await _expected_stems(conn, queries)
    if problems:
        raise SystemExit(
            "Golden set fixture errors (fix before trusting metrics):\n  " + "\n  ".join(problems)
        )

    report = EvalReport()
    for q in queries:
        qcost = QueryCost()
        result = await retrieve(q["vehicle_id"], q["query"], k=retrieve_k, cost=qcost)
        report.total_embed_tokens += qcost.embed_tokens
        report.total_embed_cost_usd += qcost.embed_cost_usd
        retrieved = [m.nhtsa_id for m in result.matches]
        expected = set(q.get("expected", []))
        stems = stems_by_query.get(q["id"], set())
        hit_rank = next(
            (
                i
                for i, m in enumerate(result.matches, 1)
                if matches_expected(m.nhtsa_id, m.communication_number, expected, stems)
            ),
            None,
        )
        report.results.append(
            QueryResult(
                id=q["id"],
                vehicle_id=q["vehicle_id"],
                query=q["query"],
                expected=q.get("expected", []),
                retrieved=retrieved,
                hit_rank=hit_rank,
                no_match=bool(q.get("no_match")),
            )
        )
    return report


def format_report(report: EvalReport) -> str:
    lines = ["", "Per-query results (scored):", ""]
    for r in report.scored:
        mark = "MISS" if r.hit_rank is None else f"@{r.hit_rank}"
        top5 = ", ".join(r.retrieved[:5]) or "(none)"
        lines.append(f"  [{mark:>4}] {r.id:<28} veh={r.vehicle_id}")
        lines.append(f"          expected={r.expected}")
        lines.append(f"          top5=[{top5}]")

    no_match = [r for r in report.results if r.no_match]
    if no_match:
        lines.append("")
        lines.append("No-match queries (should retrieve nothing relevant):")
        for r in no_match:
            top = ", ".join(r.retrieved[:3]) or "(none)"
            lines.append(f"  {r.id:<28} top3=[{top}]")

    m = report.metrics()
    lines += [
        "",
        "=" * 52,
        "  METRICS",
        "=" * 52,
        f"  scored queries : {m['queries_scored']}   (no-match: {m['queries_no_match']})",
        f"  hit@1          : {m['hit@1']:.1%}",
        f"  hit@3          : {m['hit@3']:.1%}",
        f"  hit@5          : {m['hit@5']:.1%}",
        f"  MRR            : {m['mrr']:.4f}",
        f"  embed tokens   : {m['embed_tokens_total']}",
        f"  embed cost     : ${m['embed_cost_usd_total']:.8f}",
        f"  cost / query   : ${m['cost_per_query_usd']:.8f}  (embedding only; no LLM in B1)",
        "=" * 52,
    ]
    return "\n".join(lines)


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Symptom-search retrieval eval")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--k", type=int, default=10, help="retrieval depth (>=5 for hit@5)")
    parser.add_argument("--json", action="store_true", help="emit metrics as JSON only")
    args = parser.parse_args(argv)

    try:
        report = await evaluate(args.golden, retrieve_k=args.k)
        if args.json:
            payload = {
                "metrics": report.metrics(),
                "results": [
                    {
                        "id": r.id,
                        "vehicle_id": r.vehicle_id,
                        "expected": r.expected,
                        "retrieved_top5": r.retrieved[:5],
                        "hit_rank": r.hit_rank,
                        "no_match": r.no_match,
                    }
                    for r in report.results
                ],
            }
            print(json.dumps(payload, indent=2))
        else:
            print(format_report(report))
    finally:
        await close_pool()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
