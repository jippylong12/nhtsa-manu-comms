"""Scoring and the blind judge for the extraction bake-off (JIP-1568).

Reads the ``results.jsonl`` files the harness (``extraction_bakeoff``) wrote, and
produces objective scores that need no model, then an optional blind Opus judge.
Run as ``python -m src.eval.bakeoff_score --run-dir RUN [--arms G H-text-live ...]
[--judge [--estimate]]``; ``--judge-pdf`` runs the six-way judge that reads the PDF itself and
writes into ``RUN/judge-pdf6/`` (``--judge-dir`` names another folder).

It works over an arm list: by default ``G`` and the four live arms. ``H-text`` and
``H-pdf`` (their batches were canceled) are superseded by the live re-run and are
scored only when ``--arms`` names them.

Every metric is a pure function over dicts. The database is read only for the
stored (production) output, ``associatedProducts`` and the source text, and that
read is frozen into ``<run-dir>/reference.json`` so scoring, judging and the
report can all be re-run without it.

An arm that has no ``results.jsonl`` (a live arm not run yet, a batch still running)
is simply absent: everything here works on the arms that exist and says which are
missing.
"""

import argparse
import asyncio
import json
import pathlib
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

import anthropic

from src.eval import extraction_bakeoff as eb
from src.usage import pricing
from src.usage.ledger import record_usage

FIELDS = ("summary", "symptoms", "systems", "components", "remedy", "applicability", "doc_kind")
SET_FIELDS = ("symptoms", "systems", "components")
TEXT_FIELDS = ("summary", "remedy")

# --------------------------------------------------------------------------- metric math


def _norm_set(values: Optional[list]) -> set[str]:
    return {str(v).lower().strip() for v in (values or []) if str(v).strip()}


def jaccard(a: Optional[list], b: Optional[list]) -> float:
    """Jaccard similarity of two string lists after lowercasing and stripping.

    Two empty lists agree (1.0): both documents name nothing, which is a match.
    """
    sa, sb = _norm_set(a), _norm_set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


_TOKEN = re.compile(r"\w+")


def token_f1(a: Optional[str], b: Optional[str]) -> float:
    """Token-level F1 over lowercase word bags. Two empty strings agree (1.0)."""
    ta, tb = Counter(_TOKEN.findall((a or "").lower())), Counter(_TOKEN.findall((b or "").lower()))
    if not ta and not tb:
        return 1.0
    overlap = sum((ta & tb).values())
    if not overlap:
        return 0.0
    precision, recall = overlap / sum(tb.values()), overlap / sum(ta.values())
    return 2 * precision * recall / (precision + recall)


def compare_outputs(a: dict, b: dict) -> dict[str, float]:
    """Agreement between two extraction outputs, one number per compared field."""
    out: dict[str, float] = {f: jaccard(a.get(f), b.get(f)) for f in SET_FIELDS}
    out["doc_kind"] = 1.0 if a.get("doc_kind") == b.get("doc_kind") else 0.0
    for f in TEXT_FIELDS:
        out[f] = token_f1(a.get(f), b.get(f))
    return out


# --------------------------------------------------------------------------- applicability recall

_YEAR_RANGE = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:-|–|—|to|through|thru)\s*((?:19|20)\d{2})\b", re.I
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _years_named(text: str) -> set[int]:
    """Every four-digit year in ``text``, with stated ranges (2015-2017) expanded."""
    years = {int(y) for y in _YEAR.findall(text)}
    for lo, hi in _YEAR_RANGE.findall(text):
        lo, hi = int(lo), int(hi)
        if lo <= hi and hi - lo <= 40:
            years.update(range(lo, hi + 1))
    return years


def applicability_recall(
    products: list[dict], applicability: Optional[str]
) -> dict[str, Optional[float]]:
    """Fraction of NHTSA's own years and models that the arm's applicability names.

    ``products`` is ``communications.raw -> associatedProducts``. Years are matched
    by regex; models by canonical substring (``normalize_model`` on both sides, so
    ``F-150`` meets ``F 150``), bounded so ``F 15`` is not found inside ``F 150``.
    ``recall`` pools years and models; it is ``None`` when NHTSA lists neither.
    """
    from src.communications.flatfile import normalize_model

    text = applicability or ""
    want_years = {
        int(p["productYear"]) for p in products if str(p.get("productYear") or "").isdigit()
    }
    want_models = {normalize_model(p.get("productModel")) for p in products} - {""}

    named_years = _years_named(text)
    haystack = normalize_model(text)
    hit_years = {y for y in want_years if y in named_years}
    hit_models = {
        m for m in want_models if re.search(rf"(?<![A-Z0-9]){re.escape(m)}(?![A-Z0-9])", haystack)
    }
    total = len(want_years) + len(want_models)
    return {
        "year_recall": len(hit_years) / len(want_years) if want_years else None,
        "model_recall": len(hit_models) / len(want_models) if want_models else None,
        "recall": (len(hit_years) + len(hit_models)) / total if total else None,
    }


# --------------------------------------------------------------------------- run files

EXCERPT_CHARS = 12_000


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    # "\n" only: splitlines() also breaks on U+0085/U+2028, which sit raw inside strings.
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def resolve_arms(arms: Optional[Iterable[str]] = None) -> list[str]:
    """``arms`` (or the default five) in the harness's order. Unknown names are an error."""
    wanted = list(arms) if arms else list(eb.DEFAULT_ARMS)
    for a in wanted:
        if a not in eb.ALL_ARMS:
            raise ValueError(f"unknown arm {a!r}; expected some of {', '.join(eb.ALL_ARMS)}")
    return sorted(set(wanted), key=eb.ALL_ARMS.index)


def present_arms(run_dir: pathlib.Path, arms: Optional[Iterable[str]] = None) -> list[str]:
    """The wanted arms that have a ``results.jsonl``, in harness order; the rest are missing."""
    return [a for a in resolve_arms(arms) if (pathlib.Path(run_dir) / a / "results.jsonl").exists()]


def load_results(
    run_dir: pathlib.Path, arms: Optional[Iterable[str]] = None
) -> dict[str, list[dict]]:
    return {
        a: _read_jsonl(pathlib.Path(run_dir) / a / "results.jsonl")
        for a in present_arms(run_dir, arms)
    }


def load_reference(run_dir: pathlib.Path) -> dict[int, dict]:
    """The frozen database facts per document, keyed by int ``doc_id`` (empty if absent)."""
    path = pathlib.Path(run_dir) / "reference.json"
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text()).items()}


_REFERENCE_SQL = """
    SELECT d.id AS doc_id, c.nhtsa_id, c.summary AS nhtsa_summary, c.raw,
           d.extracted_text,
           d.llm_summary, d.symptoms, d.systems, d.components, d.remedy,
           d.applicability, d.doc_kind
    FROM comm_documents d
    JOIN communications c ON c.id = d.communication_id
    WHERE d.id = ANY($1::bigint[])
"""


async def fetch_reference(doc_ids: list[int]) -> dict[int, dict]:
    """Read-only: stored output, NHTSA's summary and ``associatedProducts``, source text."""
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_REFERENCE_SQL, list(doc_ids))
    out: dict[int, dict] = {}
    for r in rows:
        raw = r["raw"]
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
        stored = None
        if r["llm_summary"] is not None:
            stored = {
                "summary": r["llm_summary"],
                "symptoms": list(r["symptoms"] or []),
                "systems": list(r["systems"] or []),
                "components": list(r["components"] or []),
                "remedy": r["remedy"] or "",
                "applicability": r["applicability"] or "",
                "doc_kind": r["doc_kind"],
            }
        out[r["doc_id"]] = {
            "nhtsa_id": r["nhtsa_id"],
            "nhtsa_summary": r["nhtsa_summary"] or raw.get("summary") or "",
            "stored": stored,
            "products": raw.get("associatedProducts") or [],
            "source": (r["extracted_text"] or "")[:EXCERPT_CHARS],
        }
    return out


async def ensure_reference(
    run_dir: pathlib.Path, refresh: bool = False, arms: Optional[Iterable[str]] = None
) -> dict[int, dict]:
    """``reference.json`` for the run, fetched from the database once and then reused."""
    run_dir = pathlib.Path(run_dir)
    if not refresh and (run_dir / "reference.json").exists():
        return load_reference(run_dir)
    doc_ids = sorted({r["doc_id"] for rows in load_results(run_dir, arms).values() for r in rows})
    ref = await fetch_reference(doc_ids)
    (run_dir / "reference.json").write_text(
        json.dumps({str(k): v for k, v in sorted(ref.items())}, ensure_ascii=False) + "\n"
    )
    return ref


# --------------------------------------------------------------------------- objective scores


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _mean_metrics(rows: list[dict[str, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for f in (*SET_FIELDS, "doc_kind", *TEXT_FIELDS):
        out[f] = _mean([r[f] for r in rows])
    return out


def _is_empty(value: Any) -> bool:
    if isinstance(value, list):
        return not any(str(v).strip() for v in value)
    return not str(value or "").strip()


def _empty_rates(outputs: list[dict]) -> dict[str, Optional[float]]:
    return {f: _mean([1.0 if _is_empty(o.get(f)) else 0.0 for o in outputs]) for f in FIELDS}


def _seconds_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def _wall_seconds(run_dir: pathlib.Path, arm: str, rows: list[dict]) -> Optional[float]:
    """A batch arm: submitted to collected (``batch.json``). A live arm: the sum of its runs
    (``live.json``)."""
    arm_dir = run_dir / arm
    if eb.arm_mode(arm) == "live":
        path = arm_dir / "live.json"
        if not path.exists():
            return None
        info = json.loads(path.read_text())
        return info.get("wall_seconds") or _seconds_between(
            info.get("started_at"), info.get("finished_at")
        )
    path = arm_dir / "batch.json"
    submitted = json.loads(path.read_text()).get("submitted_at") if path.exists() else None
    finished = max((r["collected_at"] for r in rows if r.get("collected_at")), default=None)
    return _seconds_between(submitted, finished)


GAP_FIELDS = ("symptoms", "remedy")
ALL_GAP_FIELDS = (*SET_FIELDS, "doc_kind", *TEXT_FIELDS)  # every field compare_outputs scores


def disagreement(outputs: dict[str, dict], fields: tuple[str, ...] = GAP_FIELDS) -> Optional[float]:
    """How much the arms' outputs for one document differ on ``fields`` (default symptoms
    and remedy).

    Mean over arm pairs of ``1 - mean(agreement over fields)``; ``None`` when fewer than two
    arms have an output.
    """
    arms = sorted(outputs)
    pairs = [(a, b) for i, a in enumerate(arms) for b in arms[i + 1 :]]
    if not pairs:
        return None
    gaps = []
    for a, b in pairs:
        c = compare_outputs(outputs[a], outputs[b])
        gaps.append(1.0 - sum(c[f] for f in fields) / len(fields))
    return sum(gaps) / len(gaps)


def valid_outputs_by_doc(results: dict[str, list[dict]]) -> dict[int, dict[str, dict]]:
    """``{doc_id: {arm: output}}`` for valid outputs only."""
    out: dict[int, dict[str, dict]] = {}
    for arm, rows in results.items():
        for r in rows:
            if r.get("valid") and r.get("output"):
                out.setdefault(r["doc_id"], {})[arm] = r["output"]
    return out


def score_run(
    run_dir: pathlib.Path,
    arms: Optional[Iterable[str]] = None,
    doc_ids: Optional[Iterable[int]] = None,
    reference_arm: Optional[str] = None,
) -> dict[str, Any]:
    """Every objective metric for the arms that have results. Pure: writes nothing.

    With ``reference_arm`` each other arm also gets ``vs_reference``: agreement with that
    arm's valid output on the documents both have (the same metric as ``vs_stored``, with
    the reference arm's output standing in for the stored one).

    Per-arm figures are over that arm's own documents (or only ``doc_ids``, when given, so
    arms that ran different numbers of documents compare on the same ones); ``pairwise``
    agreement is over the documents each pair shares. Cost per 1,000 documents divides by
    the documents the arm was billed for, and a live arm also gets its batch-equivalent
    (half).
    """
    run_dir = pathlib.Path(run_dir)
    wanted = resolve_arms(arms)
    results = load_results(run_dir, wanted)
    if doc_ids is not None:
        keep = set(doc_ids)
        results = {a: [r for r in rows if r["doc_id"] in keep] for a, rows in results.items()}
    reference = load_reference(run_dir)
    by_doc = valid_outputs_by_doc(results)

    arms: dict[str, Any] = {}
    for arm, rows in results.items():
        n = len(rows)
        valid = [r for r in rows if r.get("valid") and r.get("output")]
        vs_stored = [
            compare_outputs(r["output"], reference[r["doc_id"]]["stored"])
            for r in valid
            if (reference.get(r["doc_id"]) or {}).get("stored")
        ]
        recalls = [
            applicability_recall(
                reference[r["doc_id"]]["products"], r["output"].get("applicability")
            )
            for r in valid
            if reference.get(r["doc_id"])
        ]
        ref_by_doc = {
            r["doc_id"]: r["output"]
            for r in (results.get(reference_arm) or [])
            if r.get("valid") and r.get("output")
        }
        vs_reference = (
            [
                compare_outputs(r["output"], ref_by_doc[r["doc_id"]])
                for r in valid
                if r["doc_id"] in ref_by_doc
            ]
            if reference_arm and arm != reference_arm
            else None
        )
        latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
        usd = sum(r.get("cost_usd") or 0.0 for r in rows)
        billed = sum(1 for r in rows if (r.get("cost_usd") or 0.0) > 0)
        per_1000 = usd / billed * 1000 if billed else None
        mode = eb.arm_mode(arm)
        arms[arm] = {
            "model": rows[0].get("model") if rows else None,
            "mode": mode,
            "input": eb.arm_input(arm),
            "n": n,
            "valid": len(valid),
            "validity": len(valid) / n if n else None,
            "empty_rates": _empty_rates([r["output"] for r in valid]),
            "vs_stored": _mean_metrics(vs_stored),
            **({"vs_reference": _mean_metrics(vs_reference)} if vs_reference is not None else {}),
            "latency_mean_s": _mean(latencies),  # None: the arm did not record per-call time
            "applicability": {
                k: _mean([x[k] for x in recalls if x[k] is not None])
                for k in ("recall", "year_recall", "model_recall")
            }
            | {"n": sum(1 for x in recalls if x["recall"] is not None)},
            "cost": {
                "tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
                "tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
                "usd": usd,
                "billed": billed,
                "usd_per_1000": per_1000,
                # A live call costs twice what the same call costs as a batch.
                "usd_per_1000_batch_equivalent": (
                    per_1000 * pricing.BATCH_DISCOUNT
                    if per_1000 is not None and mode == "live"
                    else per_1000
                ),
            },
            "wall_seconds": _wall_seconds(run_dir, arm, rows),
        }

    pairwise: dict[str, Any] = {}
    names = list(results)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both = [o for o in by_doc.values() if a in o and b in o]
            pairwise[f"{a}|{b}"] = _mean_metrics([compare_outputs(o[a], o[b]) for o in both])

    all_valid = [o for outs in by_doc.values() for o in outs.values()]
    total_rows = sum(len(r) for r in results.values())
    overall = {
        "n": total_rows,
        "validity": (sum(a["valid"] for a in arms.values()) / total_rows) if total_rows else None,
        "empty_rates": _empty_rates(all_valid),
        "usd": sum(a["cost"]["usd"] for a in arms.values()),
    }
    gaps = {d: disagreement(o) for d, o in by_doc.items()}
    return {
        "run": str(run_dir),
        "reference_arm": reference_arm,
        "arms_present": names,
        "arms_missing": [a for a in wanted if a not in results],
        # The canceled batch arms, named so a reader knows where they went.
        "arms_superseded": [
            a for a in eb.SUPERSEDED_ARMS if a not in wanted and (run_dir / a).exists()
        ],
        "arms": arms,
        "overall": overall,
        "pairwise": pairwise,
        "disagreement": {d: g for d, g in gaps.items() if g is not None},
    }


# --------------------------------------------------------------------------- blind judge

JUDGE_MODEL = "claude-opus-5-5"
JUDGE_EFFORT = "high"
JUDGE_MAX_TOKENS = 16_000
N_HARD = 30
N_RANDOM = 30
# Adaptive thinking at high effort is not predictable, and with up to five candidates to
# weigh it thinks longer than it did for three; the estimate prices a call at the
# prompt's size (4 characters a token) plus this many output tokens.
JUDGE_OUT_TOKENS_EST = 6_000
LABELS = "ABCDE"  # one per arm, up to five
LABELS6 = "ABCDEF"  # the six-way PDF judge

_LABEL_ENUM = {"type": "string", "enum": list(LABELS)}
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": _LABEL_ENUM,
            "description": "Every output label, best first.",
        },
        "faithfulness": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": _LABEL_ENUM,
                    "unsupported_claims": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Claims in this output the source does not support. Empty if none.",
                    },
                },
                "required": ["label", "unsupported_claims"],
                "additionalProperties": False,
            },
        },
        "completeness": {
            "type": "object",
            "properties": {
                "symptoms": _LABEL_ENUM,
                "remedy": _LABEL_ENUM,
                "applicability": _LABEL_ENUM,
            },
            "required": ["symptoms", "remedy", "applicability"],
            "additionalProperties": False,
        },
        "reasoning": {"type": "string", "description": "One sentence."},
    },
    "required": ["ranking", "faithfulness", "completeness", "reasoning"],
    "additionalProperties": False,
}

_JUDGE_INSTRUCTIONS = """You are judging structured extractions of one manufacturer communication issued for a motor vehicle. \
Below is the source text (possibly cut short), then several candidate extractions, each labelled with a letter (A, B, C, ...). \
You do not know how any candidate was produced.

Judge only against the source text. For each candidate:
- faithfulness: list every claim (a symptom, part, procedure step, number, or vehicle) that the source does not support. An empty list means the candidate is faithful.
- completeness: name which candidate best captures the symptoms, which best captures the remedy, and which best captures applicability (which vehicles it covers).
Then rank all candidates, best first, and give one sentence of reasoning."""


def label_arms(
    doc_id: int, arms: list[str], seed: int = eb.SEED, labels: str = LABELS
) -> dict[str, str]:
    """``{label: arm}`` for one document: a shuffle fixed by the seed and the document id.

    Independent of the order ``arms`` arrives in, and saved by ``run_judge`` so the
    judge's labels can be mapped back to arm names afterwards. ``labels`` is ``A``-``E`` for
    the five-way judge and ``A``-``F`` (``LABELS6``) for the six-way one.
    """
    if len(arms) > len(labels):
        raise ValueError(f"{len(arms)} arms but only {len(labels)} labels")
    order = sorted(arms)
    random.Random(f"{seed}:{doc_id}").shuffle(order)
    return {labels[i]: arm for i, arm in enumerate(order)}


def build_judge_prompt(source: str, labels: dict[str, str], outputs: dict[str, dict]) -> str:
    """The judge's whole prompt: the source's first 12,000 characters and each output
    under its label. Arm names appear nowhere in it."""
    parts = [_JUDGE_INSTRUCTIONS, "", "=== SOURCE ===", (source or "")[:EXCERPT_CHARS], ""]
    for label in sorted(labels):
        parts += [
            f"=== OUTPUT {label} ===",
            json.dumps(outputs[labels[label]], indent=1, ensure_ascii=False),
            "",
        ]
    return "\n".join(parts)


def sonnet_doc_ids(path: Optional[pathlib.Path] = None) -> Optional[set[int]]:
    """The Sonnet subset's document ids, or ``None`` if that file has not been written
    (then nothing is restricted). Read at call time so tests can point it elsewhere."""
    path = pathlib.Path(path or eb.SONNET_SAMPLE_PATH)
    if not path.exists():
        return None
    return {int(r["doc_id"]) for r in json.loads(path.read_text())}


def select_judge_docs(
    by_doc: dict[int, dict[str, dict]],
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
    restrict_to: Optional[set[int]] = None,
    min_valid: int = 2,
    gap_fields: tuple[str, ...] = GAP_FIELDS,
) -> tuple[list[int], list[int]]:
    """``(hard, random)`` document ids. Eligible documents have ``min_valid`` (default two)
    or more valid outputs and, when ``restrict_to`` is given, are in it (the Sonnet subset,
    where every arm ran).

    ``hard`` is the ``n_hard`` with the largest symptom/remedy disagreement; ``random``
    is a seeded draw of ``n_random`` from the rest.
    """
    gaps = {
        d: disagreement(o, gap_fields)
        for d, o in by_doc.items()
        if len(o) >= max(min_valid, 2) and (restrict_to is None or d in restrict_to)
    }
    ranked = sorted(gaps, key=lambda d: (-gaps[d], d))
    hard = ranked[:n_hard]
    rest = sorted(ranked[n_hard:])
    rand = random.Random(seed).sample(rest, min(n_random, len(rest)))
    return hard, rand


def _generate(prompt: str) -> dict:
    """One judge call. Isolated so tests can monkeypatch it.

    Returns ``{"verdict": dict | None, "tokens_in", "tokens_out", "error"?}``. Opus 5.5
    thinks adaptively (thinking cannot be switched off), effort is ``high``, and the
    verdict comes back as structured output.
    """
    from src.anthropic_client import client

    resp = client().messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": JUDGE_EFFORT,
            "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    out: dict[str, Any] = {
        "verdict": None,
        "tokens_in": resp.usage.input_tokens,
        "tokens_out": resp.usage.output_tokens,
    }
    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        out["error"] = f"stop_reason={resp.stop_reason}"
        return out
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out["verdict"] = json.loads(text)
    except json.JSONDecodeError as e:
        out["error"] = f"invalid JSON from judge: {e}"
    return out


def _check_verdict(verdict: Any, labels: dict[str, str]) -> Optional[str]:
    """Why a verdict is unusable, or ``None``."""
    if not isinstance(verdict, dict):
        return "no verdict"
    if sorted(verdict.get("ranking") or []) != sorted(labels):
        return f"ranking {verdict.get('ranking')} is not a ranking of {sorted(labels)}"
    return None


def estimate_judge_cost(prompts: list[str]) -> float:
    """Opus list-rate cost of one call per prompt, ``batch=False``."""
    return sum(
        pricing.cost_of(JUDGE_MODEL, len(p) // 4, JUDGE_OUT_TOKENS_EST, batch=False)
        for p in prompts
    )


def _read_judge_lines(run_dir: pathlib.Path) -> list[dict]:
    path = pathlib.Path(run_dir) / "judge.jsonl"
    return _read_jsonl(path) if path.exists() else []


@dataclass
class JudgePlan:
    """The judge calls still to make, and what they could cost."""

    jobs: list  # (doc_id, {label: arm}, prompt)
    stratum: dict[int, str]
    done: set[int]
    estimate: float
    worst_case: float  # every call thinking all the way to JUDGE_MAX_TOKENS
    spent: float  # the whole bake-off so far: arms and judge, every run


def _worst_case(prompt: str) -> float:
    # 2 characters a token and every output token used.
    return pricing.cost_of(JUDGE_MODEL, len(prompt) // 2, JUDGE_MAX_TOKENS, batch=False)


def plan_judge(
    run_dir: pathlib.Path,
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
    arms: Optional[Iterable[str]] = None,
) -> JudgePlan:
    """Which documents to judge (from the Sonnet subset when its file exists), with the
    label shuffle and prompt for each, and the cost estimate. Calls nothing."""
    run_dir = pathlib.Path(run_dir)
    results = load_results(run_dir, arms)
    reference = load_reference(run_dir)
    by_doc = {d: o for d, o in valid_outputs_by_doc(results).items() if d in reference}
    hard, rand = select_judge_docs(by_doc, n_hard, n_random, seed, restrict_to=sonnet_doc_ids())
    stratum = {**{d: "random" for d in rand}, **{d: "hard" for d in hard}}
    done = {x["doc_id"] for x in _read_judge_lines(run_dir)}
    jobs = []
    for d in [*hard, *rand]:
        if d in done:
            continue
        labels = label_arms(d, list(by_doc[d]), seed)
        jobs.append((d, labels, build_judge_prompt(reference[d]["source"], labels, by_doc[d])))
    return JudgePlan(
        jobs=jobs,
        stratum=stratum,
        done=done,
        estimate=estimate_judge_cost([p for _, _, p in jobs]),
        worst_case=sum(_worst_case(p) for _, _, p in jobs),
        spent=eb.experiment_spend(run_dir),
    )


def format_plan(plan: JudgePlan) -> str:
    """The line shown before the judge runs."""
    return (
        f"judge: {len(plan.jobs)} call(s) on {JUDGE_MODEL}, estimate ${plan.estimate:.4f}, "
        f"worst case ${plan.worst_case:.4f} if every call thinks to {JUDGE_MAX_TOKENS:,} tokens; "
        f"bake-off spent ${plan.spent:.4f}, cap ${eb.CAP_USD:.2f}, "
        f"headroom ${eb.CAP_USD - plan.spent:.4f}"
    )


async def run_judge(
    run_dir: pathlib.Path,
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
    arms: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Judge the selected documents, blind, and write ``judge.jsonl`` and ``judge_labels.json``.

    The cap is checked first, against the arms' spend (collected plus committed), what
    the judge has already spent in this run, and the estimate for the calls still to
    make; past ``CAP_USD`` it raises ``SpendCapExceeded`` and nothing is called.
    A document already in ``judge.jsonl`` is not judged again.
    """
    run_dir = pathlib.Path(run_dir)
    plan = plan_judge(run_dir, n_hard, n_random, seed, arms)
    jobs, stratum, done, estimate = plan.jobs, plan.stratum, plan.done, plan.estimate
    reference = load_reference(run_dir)
    labels_path = run_dir / "judge_labels.json"
    all_labels: dict[str, dict[str, str]] = (
        json.loads(labels_path.read_text()) if labels_path.exists() else {}
    )

    # Arms (collected + committed) and judge, across every run of the bake-off.
    spent = plan.spent
    if spent + estimate > eb.CAP_USD:
        raise eb.SpendCapExceeded(
            f"refusing to judge: bake-off spent or committed ${spent:.4f} (arms and judge), "
            f"estimate ${estimate:.4f} for {len(jobs)} call(s), total "
            f"${spent + estimate:.4f} exceeds the cap of ${eb.CAP_USD:.2f}. "
            "Nothing was sent."
        )

    for d, labels, _ in jobs:
        all_labels[str(d)] = labels
    labels_path.write_text(json.dumps(all_labels, indent=1, sort_keys=True) + "\n")

    judged = 0
    stopped_at_cap = False
    with (run_dir / "judge.jsonl").open("a") as fh:
        for d, labels, prompt in jobs:
            # The estimate above is a guess; adaptive thinking can run to max_tokens.
            # Before every call, the worst case it could cost (2 characters a token,
            # every output token used) must still fit, so the cap holds on actuals.
            if spent + _worst_case(prompt) > eb.CAP_USD:
                stopped_at_cap = True
                break
            try:
                got = await asyncio.to_thread(_generate, prompt)
            except Exception as e:  # noqa: BLE001
                got = {
                    "verdict": None,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "error": f"{type(e).__name__}: {e}",
                }
            tin, tout = got.get("tokens_in") or 0, got.get("tokens_out") or 0
            cost = 0.0
            if tin or tout:
                # Metered before the verdict is judged: an unusable answer was still billed.
                cost = await record_usage(
                    kind="llm_extract",
                    model=JUDGE_MODEL,
                    tokens_in=tin,
                    tokens_out=tout,
                    batch=False,
                    meta={"bakeoff": "judge", "run": str(run_dir), "doc_id": d},
                )
            verdict = got.get("verdict")
            error = got.get("error") or _check_verdict(verdict, labels)
            fh.write(
                json.dumps(
                    {
                        "doc_id": d,
                        "nhtsa_id": reference[d]["nhtsa_id"],
                        "stratum": stratum[d],
                        "verdict": verdict if error is None else None,
                        "error": error,
                        "tokens_in": tin,
                        "tokens_out": tout,
                        "cost_usd": cost or pricing.cost_of(JUDGE_MODEL, tin, tout, batch=False),
                        "judged_at": datetime.now().astimezone().isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            fh.flush()
            spent += cost or pricing.cost_of(JUDGE_MODEL, tin, tout, batch=False)
            judged += 1
    return {
        "judged": judged,
        "skipped": len(done),
        "estimate_usd": estimate,
        "stopped_at_cap": stopped_at_cap,
    }


def load_judge(run_dir: pathlib.Path) -> tuple[list[dict], dict[int, dict[str, str]]]:
    """``(judge.jsonl lines, {doc_id: {label: arm}})``; both empty if the judge has not run."""
    path = pathlib.Path(run_dir) / "judge_labels.json"
    labels = {int(k): v for k, v in json.loads(path.read_text()).items()} if path.exists() else {}
    return _read_judge_lines(run_dir), labels


def summarize_judge(run_dir: pathlib.Path, arms: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Judge results with labels mapped back to arms: wins, mean rank, faithfulness flags.

    Covers the wanted arms that have results, plus any arm the saved label maps name, so a
    judge run made over a different arm list still summarizes. A label with no output
    behind it (the schema allows up to five) is ignored.
    """
    lines, labels = load_judge(run_dir)
    named = {a for mapping in labels.values() for a in mapping.values()}
    arms = sorted({*present_arms(run_dir, arms), *named}, key=eb.ALL_ARMS.index)
    wins = {a: 0 for a in arms}
    ranks: dict[str, list[int]] = {a: [] for a in arms}
    flags = {a: 0 for a in arms}
    flagged_docs = {a: 0 for a in arms}
    best = {f: {a: 0 for a in arms} for f in ("symptoms", "remedy", "applicability")}
    judged = 0
    for line in lines:
        verdict, mapping = line.get("verdict"), labels.get(line["doc_id"])
        if not verdict or not mapping:
            continue
        judged += 1
        wins[mapping[verdict["ranking"][0]]] += 1
        for pos, label in enumerate(verdict["ranking"], start=1):
            ranks[mapping[label]].append(pos)
        for item in verdict["faithfulness"]:
            if item.get("label") not in mapping:
                continue  # the schema allows A-E; a label with no output behind it
            n = len(item["unsupported_claims"])
            flags[mapping[item["label"]]] += n
            flagged_docs[mapping[item["label"]]] += 1 if n else 0
        for f, label in verdict["completeness"].items():
            if label in mapping:
                best[f][mapping[label]] += 1
    return {
        "judged": judged,
        "errors": sum(1 for x in lines if x.get("error")),
        "wins": wins,
        "mean_rank": {a: _mean(r) for a, r in ranks.items()},
        "faithfulness_flags": flags,
        "flagged_documents": flagged_docs,
        "completeness_wins": best,
        "usd": sum(x.get("cost_usd") or 0.0 for x in lines),
    }


# --------------------------------------------------------------------------- six-way PDF judge

# The arms the six-way judge compares: G (Flash-Lite over text), the three new PDF arms, and
# the two earlier PDF arms. Blind labels A-F, shuffled per document.
PDF6_ARMS = ("G", "GL-pdf-high", "GF-pdf-low", "H-pdf-live", "H-pdf-high", "S-pdf")
PDF6_DIR = "judge-pdf6"
PDF6_MIN_VALID = 4  # a document needs at least this many valid outputs to be judged
PDF6_FIELDS = ("summary", "symptoms", "remedy", "applicability", "systems", "components")
PDF6_STATUSES = ("correct", "partial", "missing_but_in_source", "correctly_empty", "unsupported")
# The judge reads the PDF as a document block. Opus 5.5 has a 1M-token context, so the
# 600-page request limit is the page ceiling; past it (or without a PDF) the judge falls
# back to the extracted-text excerpt and the line says so.
JUDGE_MAX_PDF_PAGES = 600
# Tokens a PDF page costs the judge: an estimate for the plan, and a generous figure for the
# per-call worst case that guards the cap (the 96-page probe measured ~1,560 on Haiku).
JUDGE_PDF_TOKENS_PER_PAGE_EST = 2_200
JUDGE_PDF_TOKENS_PER_PAGE_WORST = 3_000

_PDF6_INSTRUCTIONS_HEAD = (
    "You are judging structured extractions of one manufacturer communication issued for a "
    "motor vehicle. {source_sentence} Below are several candidate extractions, each labelled "
    "with a letter. You do not know how any candidate was produced.\n\n"
    "Judge only against the source. Read all of it, including tables. For each of the fields "
    "summary, symptoms, remedy, applicability, systems and components:\n"
    '- best: the label of the candidate that captures the field best, or "tie" if none is '
    "clearly better.\n"
    "- status, for every candidate: one of\n"
    "  correct (what it says is right and complete enough),\n"
    "  partial (right but missing part of what the source states, or only partly right),\n"
    "  missing_but_in_source (empty or silent although the source states it),\n"
    "  correctly_empty (empty, and the source has nothing for this field),\n"
    "  unsupported (states something the source does not support).\n"
    "Then rank all candidates overall, best first, and for every candidate list the claims "
    "the source does not support (an empty list if none). Give one sentence of reasoning."
)

_PDF_SENTENCE = "The source document is attached to this message as a PDF."
_EXCERPT_SENTENCE = (
    "The source document's extracted text (possibly cut short) is given below, "
    "after the instructions."
)


def pdf6_schema(labels: Iterable[str]) -> dict[str, Any]:
    """The structured-output schema for one document, built for the labels it actually has,
    so a verdict cannot rank or grade a candidate that is not there."""
    names = sorted(labels)
    label = {"type": "string", "enum": names}
    status = {
        "type": "object",
        "properties": {n: {"type": "string", "enum": list(PDF6_STATUSES)} for n in names},
        "required": names,
        "additionalProperties": False,
    }
    field = {
        "type": "object",
        "properties": {
            "best": {"type": "string", "enum": [*names, "tie"]},
            "status": status,
        },
        "required": ["best", "status"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": {f: field for f in PDF6_FIELDS},
                "required": list(PDF6_FIELDS),
                "additionalProperties": False,
            },
            "ranking": {
                "type": "array",
                "items": label,
                "description": "Every candidate label exactly once, best first (overall rank).",
            },
            "unsupported_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": label,
                        "claims": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["label", "claims"],
                    "additionalProperties": False,
                },
                "description": "One entry per candidate; claims is empty when it is faithful.",
            },
            "reasoning": {"type": "string", "description": "One sentence."},
        },
        "required": ["fields", "ranking", "unsupported_claims", "reasoning"],
        "additionalProperties": False,
    }


def build_pdf6_prompt(
    labels: dict[str, str], outputs: dict[str, dict], excerpt: Optional[str] = None
) -> str:
    """The judge's text: the instructions and each output under its label. With ``excerpt``
    the extracted text follows the instructions (the fallback); without it the PDF is
    sent as a document block next to this text. Arm names appear nowhere in it."""
    parts = [
        _PDF6_INSTRUCTIONS_HEAD.format(
            source_sentence=_PDF_SENTENCE if excerpt is None else _EXCERPT_SENTENCE
        ),
        "",
    ]
    if excerpt is not None:
        parts += ["=== SOURCE ===", excerpt[:EXCERPT_CHARS], ""]
    for label in sorted(labels):
        parts += [
            f"=== OUTPUT {label} ===",
            json.dumps(outputs[labels[label]], indent=1, ensure_ascii=False),
            "",
        ]
    return "\n".join(parts)


def _pdf_block(pdf_b64: str) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
    }


def _generate_pdf6(content: Any, schema: dict[str, Any]) -> dict:
    """One six-way judge call. Isolated so tests can monkeypatch it. Same model and
    settings as ``_generate`` (Opus 5.5, adaptive thinking, effort high, 16,000 tokens,
    structured output); the message is a PDF document block plus the prompt."""
    from src.anthropic_client import client

    resp = client().messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "effort": JUDGE_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": content}],
    )
    out: dict[str, Any] = {
        "verdict": None,
        "tokens_in": resp.usage.input_tokens,
        "tokens_out": resp.usage.output_tokens,
    }
    if resp.stop_reason not in ("end_turn", "stop_sequence"):
        out["error"] = f"stop_reason={resp.stop_reason}"
        return out
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out["verdict"] = json.loads(text)
    except json.JSONDecodeError as e:
        out["error"] = f"invalid JSON from judge: {e}"
    return out


def _check_pdf6_verdict(verdict: Any, labels: dict[str, str]) -> Optional[str]:
    """Why a six-way verdict is unusable, or ``None``. A missing or unknown label is a
    reason, never an exception, so one bad verdict cannot crash the summary."""
    if not isinstance(verdict, dict):
        return "no verdict"
    ranking = verdict.get("ranking")
    if not isinstance(ranking, list) or sorted(ranking) != sorted(labels):
        return f"ranking {ranking} is not a ranking of {sorted(labels)}"
    fields = verdict.get("fields")
    if not isinstance(fields, dict):
        return "no per-field verdicts"
    for f in PDF6_FIELDS:
        entry = fields.get(f)
        if not isinstance(entry, dict) or not isinstance(entry.get("status"), dict):
            return f"field {f} has no verdict"
        if entry.get("best") not in (*labels, "tie"):
            return f"field {f} best {entry.get('best')!r} is not a label or tie"
        if sorted(entry["status"]) != sorted(labels):
            return f"field {f} status does not cover {sorted(labels)}"
        if any(v not in PDF6_STATUSES for v in entry["status"].values()):
            return f"field {f} has an unknown status"
    if not isinstance(verdict.get("unsupported_claims"), list):
        return "no unsupported_claims"
    return None


@dataclass
class PdfJob:
    """One document for the six-way judge."""

    doc_id: int
    labels: dict[str, str]
    outputs: dict[str, dict]
    pages: int
    url: Optional[str]
    stratum: str


@dataclass
class PdfJudgePlan:
    """The six-way judge calls still to make, and what they could cost."""

    jobs: list[PdfJob]
    done: set[int]
    estimate: float
    worst_case: float
    spent: float  # the whole bake-off so far: arms, judges, pairwise, probe, every run
    out_dir: pathlib.Path


def _pdf6_prompt_tokens(job: PdfJob, per_page: int, prompt: str, chars_per_token: int) -> int:
    return job.pages * per_page + len(prompt) // chars_per_token


def plan_pdf6(
    run_dir: pathlib.Path,
    out_dir: Optional[pathlib.Path] = None,
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
    arms: Optional[Iterable[str]] = None,
    min_valid: int = PDF6_MIN_VALID,
    sample_path: Optional[pathlib.Path] = None,
    gap_fields: tuple[str, ...] = GAP_FIELDS,
) -> PdfJudgePlan:
    """Which documents the six-way judge will read, with the label shuffle for each and a
    cost estimate that assumes the PDF is sent for every one. Calls nothing.

    Documents come from the Sonnet subset (when its file exists) and need ``min_valid``
    valid outputs among the arms; ``hard`` / ``random`` are chosen as for the five-way
    judge. A document already in ``out_dir/judge.jsonl`` is not planned again, and the
    five-way ``judge.jsonl`` in ``run_dir`` is never consulted.
    """
    run_dir = pathlib.Path(run_dir)
    out_dir = pathlib.Path(out_dir) if out_dir else run_dir / PDF6_DIR
    wanted = list(arms) if arms else list(PDF6_ARMS)
    results = load_results(run_dir, wanted)
    reference = load_reference(run_dir)
    by_doc = {d: o for d, o in valid_outputs_by_doc(results).items() if d in reference}
    hard, rand = select_judge_docs(
        by_doc,
        n_hard,
        n_random,
        seed,
        restrict_to=sonnet_doc_ids(),
        min_valid=min_valid,
        gap_fields=gap_fields,
    )
    stratum = {**{d: "random" for d in rand}, **{d: "hard" for d in hard}}
    meta = {int(e["doc_id"]): e for e in eb.load_sample(sample_path or eb.SAMPLE_PATH)}
    done = {x["doc_id"] for x in _read_jsonl_if(out_dir / "judge.jsonl")}
    jobs, estimate, worst = [], 0.0, 0.0
    for d in [*hard, *rand]:
        if d in done:
            continue
        entry = meta.get(d) or {}
        job = PdfJob(
            d,
            label_arms(d, list(by_doc[d]), seed, LABELS6),
            by_doc[d],
            max(entry.get("page_count") or 1, 1),
            entry.get("url"),
            stratum[d],
        )
        jobs.append(job)
        prompt = build_pdf6_prompt(job.labels, job.outputs)
        estimate += pricing.cost_of(
            JUDGE_MODEL,
            _pdf6_prompt_tokens(job, JUDGE_PDF_TOKENS_PER_PAGE_EST, prompt, 4),
            JUDGE_OUT_TOKENS_EST,
            batch=False,
        )
        worst += _pdf6_worst_case(job, prompt, with_pdf=True)
    return PdfJudgePlan(jobs, done, estimate, worst, eb.experiment_spend(run_dir), out_dir)


def _read_jsonl_if(path: pathlib.Path) -> list[dict]:
    return _read_jsonl(path) if path.exists() else []


def _pdf6_worst_case(job: PdfJob, prompt: str, with_pdf: bool) -> float:
    """The most one call could cost: a generous prompt size and every output token."""
    tin = len(prompt) // 2 + (job.pages * JUDGE_PDF_TOKENS_PER_PAGE_WORST if with_pdf else 0)
    return pricing.cost_of(JUDGE_MODEL, tin, JUDGE_MAX_TOKENS, batch=False)


def format_pdf6_plan(plan: PdfJudgePlan) -> str:
    """The line shown before the six-way judge runs."""
    total = plan.spent + plan.estimate
    verdict = "fits under" if total <= eb.CAP_USD else "WOULD PASS"
    return (
        f"judge-pdf6: {len(plan.jobs)} call(s) on {JUDGE_MODEL} reading the PDF, "
        f"estimate ${plan.estimate:.4f}, worst case ${plan.worst_case:.4f} if every call thinks "
        f"to {JUDGE_MAX_TOKENS:,} tokens; bake-off spent ${plan.spent:.4f} (arms, judges, "
        f"pairwise, probe); estimate + spent ${total:.4f} {verdict} the ${eb.CAP_USD:.2f} cap"
    )


def _judge_source(
    job: PdfJob, pdfs: dict[int, tuple[Optional[str], Optional[str]]]
) -> tuple[Optional[str], Optional[str]]:
    """``(pdf_b64, None)`` when the judge can read the PDF, else ``(None, why not)``."""
    b64, err = pdfs.get(job.doc_id, (None, "pdf unavailable"))
    if not b64:
        return None, err or "pdf unavailable"
    if job.pages > JUDGE_MAX_PDF_PAGES:
        return None, f"too many pages ({job.pages} > {JUDGE_MAX_PDF_PAGES})"
    return b64, None


async def run_judge_pdf6(
    run_dir: pathlib.Path,
    out_dir: Optional[pathlib.Path] = None,
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
    arms: Optional[Iterable[str]] = None,
    min_valid: int = PDF6_MIN_VALID,
    sample_path: Optional[pathlib.Path] = None,
    gap_fields: tuple[str, ...] = GAP_FIELDS,
) -> dict[str, Any]:
    """Judge the selected documents blind, reading the PDF, into ``out_dir`` (default
    ``run_dir/judge-pdf6``): ``judge.jsonl``, ``judge_labels.json``. Nothing in ``run_dir``
    itself is touched.

    The cap is checked first against everything the bake-off has spent (arms, every judge,
    pairwise, probe) plus the estimate, and again before each call against that call's
    worst case. The PDF is fetched through the harness's cached download; a missing,
    over-large or over-long PDF, or one the API rejects, falls back to the 12,000-character
    excerpt, and each line records which source it used.
    """
    run_dir = pathlib.Path(run_dir)
    plan = plan_pdf6(
        run_dir, out_dir, n_hard, n_random, seed, arms, min_valid, sample_path, gap_fields
    )
    out_dir = plan.out_dir
    spent = plan.spent
    if spent + plan.estimate > eb.CAP_USD:
        raise eb.SpendCapExceeded(
            f"refusing to judge: bake-off spent or committed ${spent:.4f}, estimate "
            f"${plan.estimate:.4f} for {len(plan.jobs)} call(s), total "
            f"${spent + plan.estimate:.4f} exceeds the cap of ${eb.CAP_USD:.2f}. Nothing was sent."
        )
    reference = load_reference(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_path = out_dir / "judge_labels.json"
    all_labels: dict[str, dict[str, str]] = (
        json.loads(labels_path.read_text()) if labels_path.exists() else {}
    )
    for job in plan.jobs:
        all_labels[str(job.doc_id)] = job.labels
    labels_path.write_text(json.dumps(all_labels, indent=1, sort_keys=True) + "\n")

    pdfs: dict[int, tuple[Optional[str], Optional[str]]] = {}
    if plan.jobs:
        pdfs = await eb._fetch_pdfs(
            [{"doc_id": j.doc_id, "url": j.url} for j in plan.jobs if j.url]
        )

    judged, stopped_at_cap = 0, False
    with (out_dir / "judge.jsonl").open("a") as fh:
        for job in plan.jobs:
            b64, reason = _judge_source(job, pdfs)
            excerpt = reference[job.doc_id]["source"]
            schema = pdf6_schema(job.labels)
            pdf_prompt = build_pdf6_prompt(job.labels, job.outputs)
            text_prompt = build_pdf6_prompt(job.labels, job.outputs, excerpt)
            source = "pdf" if b64 else "excerpt"
            attempts = [(True, pdf_prompt)] if b64 else []
            attempts.append((False, text_prompt))
            tin = tout = 0
            cost = 0.0
            got: dict[str, Any] = {}
            for with_pdf, prompt in attempts:
                # The estimate is a guess; adaptive thinking can run to max_tokens. Before
                # every call its worst case must still fit, so the cap holds on actuals.
                if spent + _pdf6_worst_case(job, prompt, with_pdf) > eb.CAP_USD:
                    stopped_at_cap = True
                    got = {}
                    break
                content = (
                    [_pdf_block(b64), {"type": "text", "text": prompt}] if with_pdf else prompt
                )
                try:
                    got = await asyncio.to_thread(_generate_pdf6, content, schema)
                except Exception as e:  # noqa: BLE001
                    got = {
                        "verdict": None,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "error": f"{type(e).__name__}: {e}",
                    }
                    if with_pdf and isinstance(e, anthropic.BadRequestError):
                        reason = f"pdf rejected by the API: {e}"
                        source = "excerpt"
                        continue  # fall back to the excerpt, once
                tin, tout = got.get("tokens_in") or 0, got.get("tokens_out") or 0
                if tin or tout:
                    # Metered before the verdict is judged: an unusable answer was still billed.
                    call_cost = await record_usage(
                        kind="llm_extract",
                        model=JUDGE_MODEL,
                        tokens_in=tin,
                        tokens_out=tout,
                        batch=False,
                        meta={"bakeoff": "judge-pdf6", "run": str(run_dir), "doc_id": job.doc_id},
                    )
                    cost = call_cost or pricing.cost_of(JUDGE_MODEL, tin, tout, batch=False)
                    spent += cost
                break
            if stopped_at_cap:
                break
            verdict = got.get("verdict")
            error = got.get("error") or _check_pdf6_verdict(verdict, job.labels)
            fh.write(
                json.dumps(
                    {
                        "doc_id": job.doc_id,
                        "nhtsa_id": reference[job.doc_id]["nhtsa_id"],
                        "stratum": job.stratum,
                        "source": source,
                        "source_reason": reason,
                        "verdict": verdict if error is None else None,
                        "error": error,
                        "tokens_in": tin,
                        "tokens_out": tout,
                        "cost_usd": cost,
                        "judged_at": datetime.now().astimezone().isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            fh.flush()
            judged += 1
    return {
        "judged": judged,
        "skipped": len(plan.done),
        "estimate_usd": plan.estimate,
        "stopped_at_cap": stopped_at_cap,
        "out_dir": str(out_dir),
    }


def summarize_pdf6(out_dir: pathlib.Path) -> dict[str, Any]:
    """The six-way judge's results with labels mapped back to arms.

    ``wins`` and ``mean_rank`` come from the overall ranking; ``field_best`` counts, per
    field, the arm named best (or ``tie``); ``field_status`` counts each arm's status per
    field; ``unsupported_claims`` counts the claims flagged per arm. A line with no usable
    verdict, a label the map does not know, or a missing key is skipped or ignored, never an
    error.
    """
    lines, labels = load_judge(out_dir)
    arms = sorted(
        {a for mapping in labels.values() for a in mapping.values()}, key=eb.ALL_ARMS.index
    )
    wins = {a: 0 for a in arms}
    ranks: dict[str, list[int]] = {a: [] for a in arms}
    claims = {a: 0 for a in arms}
    claim_docs = {a: 0 for a in arms}
    best = {f: {**{a: 0 for a in arms}, "tie": 0} for f in PDF6_FIELDS}
    status = {f: {a: Counter() for a in arms} for f in PDF6_FIELDS}
    sources: Counter = Counter()
    judged = 0
    for line in lines:
        sources[line.get("source") or "unknown"] += 1
        verdict, mapping = line.get("verdict"), labels.get(line["doc_id"])
        if not isinstance(verdict, dict) or not mapping:
            continue
        ranking = [x for x in verdict.get("ranking") or [] if x in mapping]
        if not ranking:
            continue
        judged += 1
        wins[mapping[ranking[0]]] += 1
        for pos, label in enumerate(ranking, start=1):
            ranks[mapping[label]].append(pos)
        for item in verdict.get("unsupported_claims") or []:
            if item.get("label") in mapping:
                n = len(item.get("claims") or [])
                claims[mapping[item["label"]]] += n
                claim_docs[mapping[item["label"]]] += 1 if n else 0
        for f, entry in (verdict.get("fields") or {}).items():
            if f not in best or not isinstance(entry, dict):
                continue
            who = entry.get("best")
            if who == "tie":
                best[f]["tie"] += 1
            elif who in mapping:
                best[f][mapping[who]] += 1
            for label, st in (entry.get("status") or {}).items():
                if label in mapping:
                    status[f][mapping[label]][st] += 1
    return {
        "judged": judged,
        "errors": sum(1 for x in lines if x.get("error")),
        "sources": dict(sources),
        "wins": wins,
        "mean_rank": {a: _mean(r) for a, r in ranks.items()},
        "field_best": best,
        "field_status": {f: {a: dict(c) for a, c in per.items()} for f, per in status.items()},
        "unsupported_claims": claims,
        "unsupported_documents": claim_docs,
        "usd": sum(x.get("cost_usd") or 0.0 for x in lines),
    }


# --------------------------------------------------------------------------- CLI


def _print_summary(score: dict[str, Any]) -> None:
    print(f"run {score['run']}")
    print(f"arms scored: {', '.join(score['arms_present']) or 'none'}")
    if score["arms_missing"]:
        print(f"MISSING arms (no results.jsonl): {', '.join(score['arms_missing'])}")
    if score["arms_superseded"]:
        print(
            f"superseded arms: {', '.join(score['arms_superseded'])} "
            "(batch canceled, superseded by the live re-run)"
        )
    print(
        f"{'arm':<12} {'docs':>5} {'valid':>7} {'symp J':>7} {'rem F1':>7} {'appl':>6} "
        f"{'$/1000':>8} {'$/1000 batch':>12} {'wall':>8}"
    )
    f = lambda v, d=3: "-" if v is None else f"{v:.{d}f}"  # noqa: E731
    for arm, a in score["arms"].items():
        wall = "-" if a["wall_seconds"] is None else f"{a['wall_seconds'] / 60:.0f}m"
        print(
            f"{arm:<12} {a['n']:>5} {f(a['validity']):>7} {f(a['vs_stored']['symptoms']):>7} "
            f"{f(a['vs_stored']['remedy']):>7} {f(a['applicability']['recall']):>6} "
            f"{f(a['cost']['usd_per_1000'], 2):>8} "
            f"{f(a['cost']['usd_per_1000_batch_equivalent'], 2):>12} {wall:>8}"
        )


def parse_arm_names(values: Optional[list[str]]) -> Optional[list[str]]:
    """``--arms G,S-text`` and ``--arms G S-text`` both work."""
    if not values:
        return None
    return [name for v in values for name in v.split(",") if name]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.eval.bakeoff_score", description=__doc__)
    p.add_argument(
        "--run-dir",
        type=pathlib.Path,
        help="run directory (default: newest under backend/.bakeoff/)",
    )
    p.add_argument(
        "--arms",
        nargs="+",
        metavar="ARM",
        help=f"arms to score (default: {' '.join(eb.DEFAULT_ARMS)}); commas or spaces",
    )
    p.add_argument(
        "--refresh-reference", action="store_true", help="re-read reference.json from the database"
    )
    p.add_argument(
        "--judge", action="store_true", help="run the blind Opus judge (spends money, capped)"
    )
    p.add_argument(
        "--estimate",
        action="store_true",
        help="with --judge: print the call count and cost estimate, then stop without calling",
    )
    p.add_argument("--n-hard", type=int, default=N_HARD, help="judge: most-disagreeing documents")
    p.add_argument("--n-random", type=int, default=N_RANDOM, help="judge: random documents")
    p.add_argument(
        "--judge-pdf",
        action="store_true",
        help=(
            "run the six-way blind judge that reads the PDF itself (spends money, capped); "
            f"default arms: {' '.join(PDF6_ARMS)}; writes into RUN/{PDF6_DIR}/"
        ),
    )
    p.add_argument(
        "--judge-dir",
        type=pathlib.Path,
        help=f"with --judge-pdf: output folder, relative to the run dir (default {PDF6_DIR})",
    )
    p.add_argument(
        "--min-valid",
        type=int,
        default=PDF6_MIN_VALID,
        help="with --judge-pdf: valid outputs a document needs among the arms",
    )
    p.add_argument("--seed", type=int, default=eb.SEED, help="judge: seed for the draw and labels")
    p.add_argument(
        "--gap-fields",
        choices=("default", "all"),
        default="default",
        help="with --judge-pdf: pick the hard documents by symptoms+remedy (default) or by "
        "every compared field",
    )
    p.add_argument(
        "--reference-arm",
        metavar="ARM",
        help="scoring: also report each arm's agreement with this arm's valid output",
    )
    p.add_argument(
        "--subset",
        action="store_true",
        help="scoring: only the documents of the 100-document Sonnet subset",
    )
    p.add_argument(
        "--score-out",
        type=pathlib.Path,
        help="scoring: write the score here instead of RUN/score.json (which is left alone)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    run_dir = args.run_dir or eb.latest_run_dir()
    try:
        arms = parse_arm_names(args.arms)
        if run_dir is None or not present_arms(run_dir, arms):
            print("error: no run directory with results.jsonl; pass --run-dir", file=sys.stderr)
            return 2
        if args.judge_dir and not args.judge_pdf:
            raise ValueError("--judge-dir only applies to --judge-pdf")
        if args.judge_pdf:
            arms = arms or list(PDF6_ARMS)
            if not present_arms(run_dir, arms):
                print("error: none of the six-way arms has results.jsonl", file=sys.stderr)
                return 2
            asyncio.run(ensure_reference(run_dir, refresh=args.refresh_reference, arms=arms))
            out_dir = run_dir / (args.judge_dir or PDF6_DIR)
            gap = ALL_GAP_FIELDS if args.gap_fields == "all" else GAP_FIELDS
            plan = plan_pdf6(
                run_dir,
                out_dir,
                args.n_hard,
                args.n_random,
                args.seed,
                arms,
                args.min_valid,
                gap_fields=gap,
            )
            print(format_pdf6_plan(plan))
            if args.estimate:
                return 0
            out = asyncio.run(
                run_judge_pdf6(
                    run_dir,
                    out_dir,
                    args.n_hard,
                    args.n_random,
                    args.seed,
                    arms,
                    args.min_valid,
                    gap_fields=gap,
                )
            )
            print(f"judged {out['judged']} document(s) (estimate ${out['estimate_usd']:.4f})")
            if out["stopped_at_cap"]:
                print(
                    f"stopped early: the next call could have passed the ${eb.CAP_USD:.2f} cap",
                    file=sys.stderr,
                )
            summary = summarize_pdf6(out_dir)
            (out_dir / "score.json").write_text(json.dumps(summary, indent=1) + "\n")
            print(json.dumps(summary, indent=1))
            return 0
        asyncio.run(ensure_reference(run_dir, refresh=args.refresh_reference, arms=arms))
        if args.judge:
            plan = plan_judge(run_dir, args.n_hard, args.n_random, seed=args.seed, arms=arms)
            print(format_plan(plan))
            if args.estimate:
                return 0
            out = asyncio.run(
                run_judge(run_dir, args.n_hard, args.n_random, seed=args.seed, arms=arms)
            )
            print(f"judged {out['judged']} document(s) (estimate ${out['estimate_usd']:.4f})")
            if out["stopped_at_cap"]:
                print(
                    f"stopped early: the next call could have passed the ${eb.CAP_USD:.2f} cap",
                    file=sys.stderr,
                )
            print(json.dumps(summarize_judge(run_dir, arms), indent=1))
            return 0
        if args.reference_arm and args.reference_arm not in eb.ALL_ARMS:
            raise ValueError(f"unknown reference arm {args.reference_arm!r}")
        score = score_run(
            run_dir,
            arms,
            doc_ids=sonnet_doc_ids() if args.subset else None,
            reference_arm=args.reference_arm,
        )
        (args.score_out or run_dir / "score.json").write_text(
            json.dumps(score, indent=1, default=str) + "\n"
        )
        _print_summary(score)
        return 0
    except (RuntimeError, ValueError) as e:  # SpendCapExceeded is a RuntimeError
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
