"""Scoring and the blind judge for the extraction bake-off (JIP-1568).

Reads the three ``results.jsonl`` files the harness (``extraction_bakeoff``)
wrote, and produces objective scores that need no model, then an optional blind
Opus judge. Run as ``python -m src.eval.bakeoff_score --run-dir RUN [--judge]``.

Every metric is a pure function over dicts. The database is read only for the
stored (production) output, ``associatedProducts`` and the source text, and that
read is frozen into ``<run-dir>/reference.json`` so scoring, judging and the
report can all be re-run without it.

An arm that has no ``results.jsonl`` (a provider batch still running) is simply
absent: everything here works on the arms that exist and says which are missing.
"""

import argparse
import asyncio
import json
import pathlib
import random
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Optional

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


def present_arms(run_dir: pathlib.Path) -> list[str]:
    """The arms with a ``results.jsonl``, in harness order; the rest are missing."""
    return [a for a in eb.ARMS if (pathlib.Path(run_dir) / a / "results.jsonl").exists()]


def load_results(run_dir: pathlib.Path) -> dict[str, list[dict]]:
    return {
        a: _read_jsonl(pathlib.Path(run_dir) / a / "results.jsonl") for a in present_arms(run_dir)
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


async def ensure_reference(run_dir: pathlib.Path, refresh: bool = False) -> dict[int, dict]:
    """``reference.json`` for the run, fetched from the database once and then reused."""
    run_dir = pathlib.Path(run_dir)
    if not refresh and (run_dir / "reference.json").exists():
        return load_reference(run_dir)
    doc_ids = sorted({r["doc_id"] for rows in load_results(run_dir).values() for r in rows})
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


def disagreement(outputs: dict[str, dict]) -> Optional[float]:
    """How much the arms' outputs for one document differ on symptoms and remedy.

    Mean over arm pairs of ``1 - (jaccard(symptoms) + f1(remedy)) / 2``; ``None``
    when fewer than two arms have an output.
    """
    arms = sorted(outputs)
    pairs = [(a, b) for i, a in enumerate(arms) for b in arms[i + 1 :]]
    if not pairs:
        return None
    gaps = []
    for a, b in pairs:
        c = compare_outputs(outputs[a], outputs[b])
        gaps.append(1.0 - (c["symptoms"] + c["remedy"]) / 2)
    return sum(gaps) / len(gaps)


def valid_outputs_by_doc(results: dict[str, list[dict]]) -> dict[int, dict[str, dict]]:
    """``{doc_id: {arm: output}}`` for valid outputs only."""
    out: dict[int, dict[str, dict]] = {}
    for arm, rows in results.items():
        for r in rows:
            if r.get("valid") and r.get("output"):
                out.setdefault(r["doc_id"], {})[arm] = r["output"]
    return out


def score_run(run_dir: pathlib.Path) -> dict[str, Any]:
    """Every objective metric for the arms that have results. Pure: writes nothing."""
    run_dir = pathlib.Path(run_dir)
    results = load_results(run_dir)
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
        usd = sum(r.get("cost_usd") or 0.0 for r in rows)
        batch_path = run_dir / arm / "batch.json"
        submitted = (
            json.loads(batch_path.read_text()).get("submitted_at") if batch_path.exists() else None
        )
        finished = max((r["collected_at"] for r in rows if r.get("collected_at")), default=None)
        arms[arm] = {
            "model": rows[0].get("model") if rows else None,
            "n": n,
            "valid": len(valid),
            "validity": len(valid) / n if n else None,
            "empty_rates": _empty_rates([r["output"] for r in valid]),
            "vs_stored": _mean_metrics(vs_stored),
            "applicability": {
                k: _mean([x[k] for x in recalls if x[k] is not None])
                for k in ("recall", "year_recall", "model_recall")
            }
            | {"n": sum(1 for x in recalls if x["recall"] is not None)},
            "cost": {
                "tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
                "tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
                "usd": usd,
                "usd_per_1000": usd / n * 1000 if n else None,
            },
            "wall_seconds": _seconds_between(submitted, finished),
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
        "arms_present": names,
        "arms_missing": [a for a in eb.ARMS if a not in results],
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
# Adaptive thinking at high effort is not predictable; the cap check prices a call at
# the prompt's size (4 characters a token) plus this many output tokens.
JUDGE_OUT_TOKENS_EST = 4_000
LABELS = "ABC"

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
Below is the source text (possibly cut short), then several candidate extractions labelled A, B, C. \
You do not know how any candidate was produced.

Judge only against the source text. For each candidate:
- faithfulness: list every claim (a symptom, part, procedure step, number, or vehicle) that the source does not support. An empty list means the candidate is faithful.
- completeness: name which candidate best captures the symptoms, which best captures the remedy, and which best captures applicability (which vehicles it covers).
Then rank all candidates, best first, and give one sentence of reasoning."""


def label_arms(doc_id: int, arms: list[str], seed: int = eb.SEED) -> dict[str, str]:
    """``{label: arm}`` for one document: a shuffle fixed by the seed and the document id.

    Independent of the order ``arms`` arrives in, and saved by ``run_judge`` so the
    judge's labels can be mapped back to arm names afterwards.
    """
    order = sorted(arms)
    random.Random(f"{seed}:{doc_id}").shuffle(order)
    return {LABELS[i]: arm for i, arm in enumerate(order)}


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


def select_judge_docs(
    by_doc: dict[int, dict[str, dict]],
    n_hard: int = N_HARD,
    n_random: int = N_RANDOM,
    seed: int = eb.SEED,
) -> tuple[list[int], list[int]]:
    """``(hard, random)`` document ids. Eligible documents have two or more valid outputs.

    ``hard`` is the ``n_hard`` with the largest symptom/remedy disagreement; ``random``
    is a seeded draw of ``n_random`` from the rest.
    """
    gaps = {d: disagreement(o) for d, o in by_doc.items() if len(o) >= 2}
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


async def run_judge(
    run_dir: pathlib.Path, n_hard: int = N_HARD, n_random: int = N_RANDOM, seed: int = eb.SEED
) -> dict[str, Any]:
    """Judge the selected documents, blind, and write ``judge.jsonl`` and ``judge_labels.json``.

    The cap is checked first, against the arms' spend (collected plus committed), what
    the judge has already spent in this run, and the estimate for the calls still to
    make; past ``CAP_USD`` it raises ``SpendCapExceeded`` and nothing is called.
    A document already in ``judge.jsonl`` is not judged again.
    """
    run_dir = pathlib.Path(run_dir)
    results = load_results(run_dir)
    reference = load_reference(run_dir)
    by_doc = {d: o for d, o in valid_outputs_by_doc(results).items() if d in reference}
    hard, rand = select_judge_docs(by_doc, n_hard, n_random, seed)
    stratum = {**{d: "random" for d in rand}, **{d: "hard" for d in hard}}

    done = {x["doc_id"] for x in _read_judge_lines(run_dir)}
    todo = [d for d in [*hard, *rand] if d not in done]
    labels_path = run_dir / "judge_labels.json"
    all_labels: dict[str, dict[str, str]] = (
        json.loads(labels_path.read_text()) if labels_path.exists() else {}
    )
    jobs = []
    for d in todo:
        labels = label_arms(d, list(by_doc[d]), seed)
        jobs.append((d, labels, build_judge_prompt(reference[d]["source"], labels, by_doc[d])))

    estimate = estimate_judge_cost([p for _, _, p in jobs])
    # Arms (collected + committed) and judge, across every run of the bake-off.
    spent = eb.experiment_spend(run_dir)
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
            worst = pricing.cost_of(JUDGE_MODEL, len(prompt) // 2, JUDGE_MAX_TOKENS, batch=False)
            if spent + worst > eb.CAP_USD:
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


def summarize_judge(run_dir: pathlib.Path) -> dict[str, Any]:
    """Judge results with labels mapped back to arms: wins, mean rank, faithfulness flags."""
    lines, labels = load_judge(run_dir)
    arms = present_arms(run_dir)
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
                continue  # the schema allows A-C; a label with no output behind it
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


# --------------------------------------------------------------------------- CLI


def _print_summary(score: dict[str, Any]) -> None:
    print(f"run {score['run']}")
    print(f"arms scored: {', '.join(score['arms_present']) or 'none'}")
    if score["arms_missing"]:
        print(f"MISSING arms (no results.jsonl): {', '.join(score['arms_missing'])}")
    print(
        f"{'arm':<8} {'valid':>7} {'symp J':>7} {'rem F1':>7} {'appl':>6} {'$/1000':>8} {'wall':>8}"
    )
    f = lambda v, d=3: "-" if v is None else f"{v:.{d}f}"  # noqa: E731
    for arm, a in score["arms"].items():
        wall = "-" if a["wall_seconds"] is None else f"{a['wall_seconds'] / 60:.0f}m"
        print(
            f"{arm:<8} {f(a['validity']):>7} {f(a['vs_stored']['symptoms']):>7} "
            f"{f(a['vs_stored']['remedy']):>7} {f(a['applicability']['recall']):>6} "
            f"{f(a['cost']['usd_per_1000'], 2):>8} {wall:>8}"
        )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.eval.bakeoff_score", description=__doc__)
    p.add_argument(
        "--run-dir",
        type=pathlib.Path,
        help="run directory (default: newest under backend/.bakeoff/)",
    )
    p.add_argument(
        "--refresh-reference", action="store_true", help="re-read reference.json from the database"
    )
    p.add_argument(
        "--judge", action="store_true", help="run the blind Opus judge (spends money, capped)"
    )
    p.add_argument("--n-hard", type=int, default=N_HARD, help="judge: most-disagreeing documents")
    p.add_argument("--n-random", type=int, default=N_RANDOM, help="judge: random documents")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    run_dir = args.run_dir or eb.latest_run_dir()
    if run_dir is None or not present_arms(run_dir):
        print("error: no run directory with results.jsonl; pass --run-dir", file=sys.stderr)
        return 2
    try:
        asyncio.run(ensure_reference(run_dir, refresh=args.refresh_reference))
        if args.judge:
            out = asyncio.run(run_judge(run_dir, args.n_hard, args.n_random))
            print(f"judged {out['judged']} document(s) (estimate ${out['estimate_usd']:.4f})")
            if out["stopped_at_cap"]:
                print(
                    f"stopped early: the next call could have passed the ${eb.CAP_USD:.2f} cap",
                    file=sys.stderr,
                )
            print(json.dumps(summarize_judge(run_dir), indent=1))
            return 0
        score = score_run(run_dir)
        (run_dir / "score.json").write_text(json.dumps(score, indent=1, default=str) + "\n")
        _print_summary(score)
        return 0
    except (RuntimeError, ValueError) as e:  # SpendCapExceeded is a RuntimeError
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
