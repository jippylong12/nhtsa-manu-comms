"""Full-pipeline quality eval for symptom search (JIP-381).

Where ``run_eval`` measures retrieval only (no LLM), this runs the whole
pipeline through ``service.search`` so it can score the compose-layer behaviour
that retrieval metrics cannot see: grounding, hallucinated citations, and
refusal precision on the adversarial no-match queries.

    python -m src.eval.quality_eval                      # default model
    python -m src.eval.quality_eval --model gemini-3-flash-preview
    python -m src.eval.quality_eval --hardest 10 --json  # model-comparison subset

Metrics reported:
  * match queries  : grounded rate, false-refusal rate, hallucinated-citation count,
                     end-to-end hit@1/3/5 (expected id present in returned matches).
  * no-match queries: refusal rate (must be 1.0 — every adversarial query refused),
                     and any that slipped through as a false answer.
  * cost           : per-query and aggregate (embedding + composition).
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.db import close_pool
from src.eval.run_eval import GOLDEN_PATH, load_golden
from src.symptom_search import service

# Queries treated as "hardest" for the model comparison: colloquial, vague,
# multi-system, and the adversarial no-match set.
HARDEST_IDS = [
    "screen_bricks_black_colloquial",
    "ghost_braking_colloquial",
    "damp_floor_vague",
    "handsfree_cruise_flaky_colloquial",
    "hv_warning_charge_capped",
    "nm_death_wobble",
    "nm_gas_mileage",
    "service_hv_no_heat",
    "all_safety_features_dead",
    "lost_google_services",
]


@dataclass
class Row:
    id: str
    no_match: bool
    refused: bool
    grounded: bool
    hit_rank: int | None
    hallucinated: list[str]
    cost_usd: float
    answer: str
    cited: list[str]


@dataclass
class QualityReport:
    model: str
    rows: list[Row] = field(default_factory=list)

    @property
    def match_rows(self):
        return [r for r in self.rows if not r.no_match]

    @property
    def nomatch_rows(self):
        return [r for r in self.rows if r.no_match]

    def metrics(self) -> dict:
        matches = self.match_rows
        nm = self.nomatch_rows
        grounded = sum(1 for r in matches if r.grounded)
        false_refusals = sum(1 for r in matches if r.refused)
        halluc = sum(len(r.hallucinated) for r in self.rows)

        def hit_at(k):
            if not matches:
                return 0.0
            return sum(1 for r in matches if r.hit_rank and r.hit_rank <= k) / len(matches)

        nm_refused = sum(1 for r in nm if r.refused)
        total_cost = sum(r.cost_usd for r in self.rows)
        return {
            "model": self.model,
            "match_queries": len(matches),
            "grounded": grounded,
            "grounded_rate": round(grounded / len(matches), 4) if matches else 0.0,
            "false_refusals": false_refusals,
            "hallucinated_citations": halluc,
            "hit@1": round(hit_at(1), 4),
            "hit@3": round(hit_at(3), 4),
            "hit@5": round(hit_at(5), 4),
            "no_match_queries": len(nm),
            "no_match_refused": nm_refused,
            "refusal_precision_on_nomatch": round(nm_refused / len(nm), 4) if nm else 1.0,
            "false_answers_on_nomatch": len(nm) - nm_refused,
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_query_usd": round(total_cost / len(self.rows), 6) if self.rows else 0.0,
            "max_cost_usd": round(max((r.cost_usd for r in self.rows), default=0.0), 6),
        }


async def run_quality(
    golden_path: Path, model: str | None, only_ids: set[str] | None
) -> QualityReport:
    queries = load_golden(golden_path)
    if only_ids:
        queries = [q for q in queries if q["id"] in only_ids]

    report = QualityReport(model=model or "default")
    for q in queries:
        res = await service.search(q["vehicle_id"], q["query"], model=model)
        returned = [m["nhtsa_id"] for m in res["matches"]]
        expected = set(q.get("expected", []))
        hit_rank = next((i for i, nid in enumerate(returned, 1) if nid in expected), None)
        hallucinated = [c for c in res["cited_ids"] if c not in set(returned)]
        report.rows.append(
            Row(
                id=q["id"],
                no_match=bool(q.get("no_match")),
                refused=res["refused"],
                grounded=(not res["refused"]) and bool(res["cited_ids"]),
                hit_rank=hit_rank,
                hallucinated=hallucinated,
                cost_usd=res["cost"]["total_usd"],
                answer=res["answer"],
                cited=res["cited_ids"],
            )
        )
    return report


def format_report(report: QualityReport) -> str:
    m = report.metrics()
    lines = [f"\nMODEL: {m['model']}", ""]
    lines.append("Match queries:")
    for r in report.match_rows:
        tag = "REFUSED!" if r.refused else (f"@{r.hit_rank}" if r.hit_rank else "miss")
        h = f" HALLUC={r.hallucinated}" if r.hallucinated else ""
        lines.append(f"  [{tag:>8}] {r.id:<34} cited={r.cited}{h}")
    lines.append("\nNo-match (adversarial) queries:")
    for r in report.nomatch_rows:
        tag = "refused" if r.refused else "FALSE-ANSWER!"
        lines.append(f"  [{tag:>12}] {r.id:<24} cited={r.cited}")
    lines += [
        "",
        "=" * 56,
        f"  grounded rate         : {m['grounded']}/{m['match_queries']} "
        f"({m['grounded_rate']:.1%})",
        f"  false refusals        : {m['false_refusals']}",
        f"  hallucinated cites    : {m['hallucinated_citations']}  (must be 0)",
        f"  hit@1 / hit@3 / hit@5 : {m['hit@1']:.1%} / {m['hit@3']:.1%} / {m['hit@5']:.1%}",
        f"  no-match refused      : {m['no_match_refused']}/{m['no_match_queries']} "
        f"({m['refusal_precision_on_nomatch']:.1%})",
        f"  false answers on n/m  : {m['false_answers_on_nomatch']}  (must be 0)",
        f"  total cost            : ${m['total_cost_usd']:.6f}",
        f"  avg cost / query      : ${m['avg_cost_per_query_usd']:.6f}",
        f"  max cost / query      : ${m['max_cost_usd']:.6f}",
        "=" * 56,
    ]
    return "\n".join(lines)


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Symptom-search full-pipeline quality eval")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--model", type=str, default=None, help="override composition model")
    parser.add_argument("--hardest", action="store_true", help="only the hardest-query subset")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    only = set(HARDEST_IDS) if args.hardest else None
    try:
        report = await run_quality(args.golden, args.model, only)
        if args.json:
            print(json.dumps({"metrics": report.metrics()}, indent=2))
        else:
            print(format_report(report))
    finally:
        await close_pool()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
