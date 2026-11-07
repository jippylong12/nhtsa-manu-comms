"""Side-by-side report for the extraction bake-off (JIP-1568).

``python -m src.eval.bakeoff_report --run-dir RUN [--arms ...]`` writes ``report.md``
and ``report.html`` into the run directory. It reads only files already there
(``results.jsonl`` per arm, ``reference.json``, ``live.json`` / ``batch.json`` for wall
time, and ``judge.jsonl`` plus ``judge_labels.json`` when the judge has run), so it
needs no database and no key.

The arms are ``G`` and the four live arms unless ``--arms`` says otherwise. The
headline has one row per arm, and the twenty side-by-side documents are drawn from the
Sonnet subset (the documents every arm ran) so each shows all five outputs. The HTML is
one file with inline CSS and no script, readable in light and dark; its column grid
wraps to fewer columns on narrow screens. Arms with no results yet are named in a
"Missing arms" line, and the canceled batch arms in a "Superseded arms" line.

``--pdf6`` writes only ``<run>/judge-pdf6/summary.md``: the six-way PDF judge's results as
plain-text tables next to the per-arm objective scores, all over the Sonnet subset so every
arm is measured on the same documents. ``report.html`` is not touched.
"""

import argparse
import html
import pathlib
import random
import sys
from typing import Any, Iterable, Optional

from src.eval import bakeoff_score as bs
from src.eval import extraction_bakeoff as eb

HARD_DOCS = 10
RANDOM_DOCS = 10
MD_EXCERPT_CHARS = 600
HTML_EXCERPT_CHARS = 3000


# --------------------------------------------------------------------------- formatting


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _usd(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"${v:,.{digits}f}"


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")


def _field_text(output: dict, field: str) -> str:
    value = output.get(field)
    if isinstance(value, list):
        return "; ".join(value) if value else "(none)"
    return str(value) if str(value or "").strip() else "(empty)"


# --------------------------------------------------------------------------- what to show


def pick_documents(
    gaps: dict[int, float], hard: int = HARD_DOCS, rand: int = RANDOM_DOCS, seed: int = eb.SEED
) -> list[tuple[int, str]]:
    """``[(doc_id, "largest disagreement" | "random")]``: the ``hard`` most-disagreeing
    documents, then a seeded random ``rand`` of the others."""
    ranked = sorted(gaps, key=lambda d: (-gaps[d], d))
    top = ranked[:hard]
    rest = sorted(ranked[hard:])
    drawn = random.Random(seed).sample(rest, min(rand, len(rest)))
    return [(d, "largest disagreement") for d in top] + [(d, "random") for d in sorted(drawn)]


def _judge_note(
    doc_id: int, lines: dict[int, dict], labels: dict[int, dict[str, str]]
) -> Optional[dict]:
    line, mapping = lines.get(doc_id), labels.get(doc_id)
    verdict = (line or {}).get("verdict")
    if not verdict or not mapping:
        return None
    return {
        "ranking": [mapping[x] for x in verdict["ranking"]],
        "reasoning": verdict["reasoning"],
        "unsupported": {
            mapping[i["label"]]: i["unsupported_claims"]
            for i in verdict["faithfulness"]
            if i["unsupported_claims"]
        },
        "best": {f: mapping[x] for f, x in verdict["completeness"].items() if x in mapping},
    }


def _collect(run_dir: pathlib.Path, arms: Optional[Iterable[str]] = None) -> dict[str, Any]:
    score = bs.score_run(run_dir, arms)
    results = bs.load_results(run_dir, arms)
    by_doc = bs.valid_outputs_by_doc(results)
    judge_lines, labels = bs.load_judge(run_dir)
    lines = {x["doc_id"]: x for x in judge_lines}
    reference = bs.load_reference(run_dir)
    # The side-by-side documents come from the Sonnet subset, where every arm ran.
    subset = bs.sonnet_doc_ids()
    gaps = {d: g for d, g in score["disagreement"].items() if subset is None or d in subset}
    docs = []
    for doc_id, why in pick_documents(gaps):
        ref = reference.get(doc_id) or {}
        docs.append(
            {
                "doc_id": doc_id,
                "why": why,
                "nhtsa_id": ref.get("nhtsa_id") or str(doc_id),
                "nhtsa_summary": ref.get("nhtsa_summary") or "",
                "source": ref.get("source") or "",
                "outputs": by_doc.get(doc_id, {}),
                "gap": score["disagreement"][doc_id],
                "judge": _judge_note(doc_id, lines, labels),
            }
        )
    return {
        "score": score,
        "judge": bs.summarize_judge(run_dir, arms),
        "judged": bool(judge_lines),
        "docs": docs,
    }


# --------------------------------------------------------------------------- headline rows

HEADLINE = (
    "Arm",
    "Model",
    "Input",
    "Docs",
    "Validity",
    "Agreement with stored",
    "Applicability recall",
    "Judge wins / avg rank",
    "Faithfulness flags",
    "Cost actual",
    "Cost per 1,000 docs",
    "Batch-equivalent cost per 1,000 docs",
    "Wall time",
    "Batch or live",
)
INPUT_LABEL = {"text": "extracted text", "pdf": "PDF"}


def _headline_rows(data: dict[str, Any]) -> list[list[str]]:
    rows = []
    judge = data["judge"]
    for arm, a in data["score"]["arms"].items():
        stored = a["vs_stored"]
        rows.append(
            [
                arm,
                a["model"] or "—",
                INPUT_LABEL[a["input"]],
                str(a["n"]),
                f"{_pct(a['validity'])} ({a['valid']}/{a['n']})",
                f"{_num(stored['symptoms'])} / {_num(stored['remedy'])}",
                _num(a["applicability"]["recall"]),
                (
                    f"{judge['wins'].get(arm, 0)} / {_num(judge['mean_rank'].get(arm))}"
                    if data["judged"]
                    else "—"
                ),
                (
                    f"{judge['faithfulness_flags'].get(arm, 0)} in {judge['flagged_documents'].get(arm, 0)} docs"
                    if data["judged"]
                    else "—"
                ),
                _usd(a["cost"]["usd"], 4),
                _usd(a["cost"]["usd_per_1000"], 3),
                _usd(a["cost"]["usd_per_1000_batch_equivalent"], 3),
                _duration(a["wall_seconds"]),
                a["mode"],
            ]
        )
    return rows


def _empty_rows(score: dict[str, Any]) -> list[list[str]]:
    rows = []
    for arm, a in score["arms"].items():
        rows.append([arm] + [_pct(a["empty_rates"][f]) for f in bs.FIELDS])
    rows.append(["overall"] + [_pct(score["overall"]["empty_rates"][f]) for f in bs.FIELDS])
    return rows


def _pairwise_rows(score: dict[str, Any]) -> list[list[str]]:
    cols = (*bs.SET_FIELDS, "doc_kind", *bs.TEXT_FIELDS)
    rows = [
        [f"{arm} vs stored", str(a["vs_stored"]["n"])] + [_num(a["vs_stored"][c]) for c in cols]
        for arm, a in score["arms"].items()
    ]
    rows += [
        [k.replace("|", " vs "), str(v["n"])] + [_num(v[c]) for c in cols]
        for k, v in score["pairwise"].items()
    ]
    return rows


_PAIR_HEAD = (
    "Pair",
    "Docs",
    "Symptoms",
    "Systems",
    "Components",
    "doc_kind",
    "Summary F1",
    "Remedy F1",
)
_EMPTY_HEAD = ("Arm", *bs.FIELDS)


def _missing_line(score: dict[str, Any]) -> Optional[str]:
    if not score["arms_missing"]:
        return None
    return (
        "Missing arms: "
        + ", ".join(score["arms_missing"])
        + " (no results.jsonl yet; scored without them)"
    )


def _superseded_line(score: dict[str, Any]) -> Optional[str]:
    if not score["arms_superseded"]:
        return None
    return (
        "Superseded arms: "
        + ", ".join(score["arms_superseded"])
        + " (batch canceled, superseded by live re-run)"
    )


def _pricing_note(score: dict[str, Any]) -> Optional[str]:
    """Why the cost columns are not like for like, when the arms mix batch and live."""
    if len({a["mode"] for a in score["arms"].values()}) < 2:
        return None
    return (
        "G ran through the Batch API at half price; the other arms ran live through the "
        "Messages API at list price. The batch-equivalent column is a live arm's cost per "
        "1,000 documents times 0.5, so every row compares at the batch price."
    )


_JUDGE_HEAD = (
    "Arm",
    "Wins",
    "Mean rank",
    "Unsupported claims",
    "Best symptoms",
    "Best remedy",
    "Best applicability",
)
NO_JUDGE = "Judge has not run for this run."


def _judge_intro(data: dict[str, Any]) -> str:
    j = data["judge"]
    return f"{j['judged']} documents judged blind by {bs.JUDGE_MODEL} ({j['errors']} unusable), ${j['usd']:.2f}."


def _judge_rows(data: dict[str, Any]) -> list[list[str]]:
    j, cw = data["judge"], data["judge"]["completeness_wins"]
    return [
        [arm, str(j["wins"][arm]), _num(j["mean_rank"][arm]), str(j["faithfulness_flags"][arm])]
        + [str(cw[f][arm]) for f in ("symptoms", "remedy", "applicability")]
        for arm in data["score"]["arms_present"]
    ]


# --------------------------------------------------------------------------- markdown


def _md_table(head: tuple, rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
        *("| " + " | ".join(r) + " |" for r in rows),
    ]


def render_markdown(data: dict[str, Any]) -> str:
    score = data["score"]
    out = ["# Extraction bake-off report", "", f"Run: `{score['run']}`", ""]
    for note in (_missing_line(score), _superseded_line(score)):
        if note:
            out += [f"**{note}**", ""]
    out += ["## Headline", "", *_md_table(HEADLINE, _headline_rows(data)), ""]
    if _pricing_note(score):
        out += [f"_{_pricing_note(score)}_", ""]
    out += ["## Agreement", "", *_md_table(_PAIR_HEAD, _pairwise_rows(score)), ""]
    out += ["## Empty fields", "", *_md_table(_EMPTY_HEAD, _empty_rows(score)), ""]
    if data["judged"]:
        out += [
            "## Judge",
            "",
            _judge_intro(data),
            "",
            *_md_table(_JUDGE_HEAD, _judge_rows(data)),
            "",
        ]
    else:
        out += ["## Judge", "", NO_JUDGE, ""]
    out += [f"## Side by side ({len(data['docs'])} documents)", ""]
    for d in data["docs"]:
        out += [f"\n### Document {d['nhtsa_id']} (doc {d['doc_id']}, {d['why']})", ""]
        out += [f"**NHTSA summary.** {d['nhtsa_summary']}", ""]
        excerpt = d["source"][:MD_EXCERPT_CHARS]
        out += ["**Source excerpt.**", "", "> " + excerpt.replace("\n", "\n> "), ""]
        for arm in score["arms_present"]:
            out += [f"**{arm}**", ""]
            if arm in d["outputs"]:
                out += [f"- {f}: {_field_text(d['outputs'][arm], f)}" for f in bs.FIELDS]
            else:
                out += ["- (no valid output)"]
            out += [""]
        if d["judge"]:
            j = d["judge"]
            out += [f"**Judge:** {' > '.join(j['ranking'])}. {j['reasoning']}", ""]
            out += [
                f"- unsupported in {arm}: {'; '.join(c)}" for arm, c in j["unsupported"].items()
            ]
            out += [""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- html

CSS = """
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1c1c1e;--muted:#63636b;--card:#fff;--line:#e2e2e0;--accent:#2f5fd0;--warn:#b4521e;--chip:#eef1fb}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#e9e9ec;--muted:#9a9aa4;--card:#1d1d20;--line:#34343a;--accent:#8fb0ff;--warn:#f0a070;--chip:#262a3a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1480px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.2rem;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:1rem;margin:0}
.muted{color:var(--muted)}.warn{color:var(--warn);font-weight:600}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
section.doc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
section.doc header{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
.chip{background:var(--chip);color:var(--accent);border-radius:999px;padding:1px 9px;font-size:.78rem}
.nhtsa{margin:6px 0}
details{margin:6px 0}summary{cursor:pointer;color:var(--accent)}
pre.src{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;font-size:.82rem;max-height:320px;overflow:auto}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px}
.col{border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0}
.col.none{border-style:dashed;color:var(--muted)}
.col h4{margin:0 0 2px;font-size:.9rem;color:var(--accent)}
.col .meta{font-size:.75rem;color:var(--muted);margin-bottom:4px}
.col .rank{font-size:.75rem;background:var(--chip);color:var(--accent);border-radius:999px;padding:0 7px;margin-left:6px}
.note{margin:8px 0 0}
dl{margin:0}dt{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-top:6px}
dd{margin:0;word-break:break-word}
.judge{margin-top:10px;border-left:3px solid var(--accent);padding:2px 10px}
"""


def _h(value: Any) -> str:
    return html.escape(str(value))


def _html_table(head: tuple, rows: list[list[str]]) -> str:
    th = "".join(f"<th>{_h(c)}</th>" for c in head)
    body = "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _html_doc(d: dict[str, Any], arms: dict[str, dict], order: list[str]) -> str:
    """One document: a column per arm in headline order, so the arms line up document to
    document. An arm with no valid output for it still gets a (dashed) column."""
    ranking = d["judge"]["ranking"] if d["judge"] else []
    cols = []
    for arm in order:
        info = arms[arm]
        meta = f"{info['model']} · {INPUT_LABEL[info['input']]} · {info['mode']}"
        rank = (
            f'<span class="rank">judge #{ranking.index(arm) + 1}</span>' if arm in ranking else ""
        )
        head = f'<h4>{_h(arm)}{rank}</h4><div class="meta">{_h(meta)}</div>'
        if arm in d["outputs"]:
            fields = "".join(
                f"<dt>{_h(f)}</dt><dd>{_h(_field_text(d['outputs'][arm], f))}</dd>"
                for f in bs.FIELDS
            )
            cols.append(f'<div class="col">{head}<dl>{fields}</dl></div>')
        else:
            cols.append(f'<div class="col none">{head}<p>(no valid output)</p></div>')
    judge = ""
    if d["judge"]:
        j = d["judge"]
        claims = "".join(
            f"<div class='warn'>Unsupported in {_h(arm)}: {_h('; '.join(c))}</div>"
            for arm, c in j["unsupported"].items()
        )
        judge = (
            f'<div class="judge"><strong>Judge:</strong> {_h(" > ".join(j["ranking"]))}. '
            f'{_h(j["reasoning"])}{claims}</div>'
        )
    return (
        f'<section class="doc" id="doc-{d["doc_id"]}">'
        f'<header><h3>Document {_h(d["nhtsa_id"])}</h3><span class="chip">{_h(d["why"])}</span>'
        f'<span class="muted">doc {d["doc_id"]}, disagreement {d["gap"]:.2f}</span></header>'
        f'<p class="nhtsa"><strong>NHTSA summary.</strong> {_h(d["nhtsa_summary"])}</p>'
        f'<details><summary>Source excerpt</summary><pre class="src">{_h(d["source"][:HTML_EXCERPT_CHARS])}</pre></details>'
        f'<div class="cols">{"".join(cols)}</div>{judge}</section>'
    )


def render_html(data: dict[str, Any]) -> str:
    score = data["score"]
    missing, superseded, pricing = (
        _missing_line(score),
        _superseded_line(score),
        _pricing_note(score),
    )
    judge_html = (
        f"<p class='muted'>{_h(_judge_intro(data))}</p>"
        + _html_table(_JUDGE_HEAD, _judge_rows(data))
        if data["judged"]
        else f"<p class='muted'>{NO_JUDGE}</p>"
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta name='color-scheme' content='light dark'>",
        "<title>Extraction bake-off report</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<h1>Extraction bake-off report</h1>",
        f"<div class='muted'>Run {_h(score['run'])}</div>",
        f"<p class='warn'>{_h(missing)}</p>" if missing else "",
        f"<p class='muted'>{_h(superseded)}</p>" if superseded else "",
        "<h2>Headline</h2>",
        _html_table(HEADLINE, _headline_rows(data)),
        f"<p class='muted note'>{_h(pricing)}</p>" if pricing else "",
        "<h2>Agreement</h2>",
        _html_table(_PAIR_HEAD, _pairwise_rows(score)),
        "<h2>Empty fields</h2>",
        _html_table(_EMPTY_HEAD, _empty_rows(score)),
        "<h2>Judge</h2>",
        judge_html,
        f"<h2>Side by side ({len(data['docs'])} documents)</h2>",
        *(_html_doc(d, score["arms"], score["arms_present"]) for d in data["docs"]),
        "</main></body></html>",
    ]
    return "".join(parts)


# --------------------------------------------------------------------------- six-way summary

SUMMARY_NAME = "summary.md"
EXAMPLE_VALUE_CHARS = 90
EXAMPLE_REASON_CHARS = 300
_STATUS_ORDER = ("correct", "partial", "missing_but_in_source", "correctly_empty", "unsupported")
_STATUS_SHORT = {
    "correct": "ok",
    "partial": "part",
    "missing_but_in_source": "miss",
    "correctly_empty": "empty",
    "unsupported": "unsup",
}


def _text_table(head: tuple[str, ...], rows: list[list[str]]) -> str:
    """Aligned plain-text columns: first column left, the rest right."""
    grid = [list(head), *[[str(c) for c in r] for r in rows]]
    widths = [max(len(r[i]) for r in grid) for i in range(len(head))]

    def fmt(r: list[str]) -> str:
        cells = [r[0].ljust(widths[0])] + [r[i].rjust(widths[i]) for i in range(1, len(r))]
        return "  ".join(cells).rstrip()

    return "\n".join([fmt(grid[0]), "  ".join("-" * w for w in widths), *map(fmt, grid[1:])])


def _live_and_batch_per_1000(
    arm: str, a: dict[str, Any]
) -> tuple[Optional[float], Optional[float]]:
    """``(live $/1k, batch-equivalent $/1k)``: a live arm's recorded cost is the live one and
    half of it is the batch one; a batch arm's recorded cost is the batch one and double it
    is what the same calls cost live."""
    per = a["cost"]["usd_per_1000"]
    if per is None:
        return None, None
    if a["mode"] == "live":
        return per, per * eb.pricing.BATCH_DISCOUNT
    return per / eb.pricing.BATCH_DISCOUNT, per


def _error_reasons(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        if r.get("error"):
            key = str(r["error"]).split(":")[0].split("(")[0].strip()[:40]
            counts[key] = counts.get(key, 0) + 1
    return "; ".join(f"{n} x {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])) or "-"


def _pick_examples(
    run_dir: pathlib.Path, out_dir: pathlib.Path, arms: list[str], count: int = 3
) -> list[str]:
    """Up to ``count`` documents where the arms differ most, each as a few short lines:
    the document and field, each arm's status and value, and the judge's reason."""
    lines, labels = bs.load_judge(out_dir)
    outputs = bs.valid_outputs_by_doc(bs.load_results(run_dir, arms))
    scored: list[tuple[tuple, int, str, dict, dict]] = []
    for line in lines:
        verdict, mapping = line.get("verdict"), labels.get(line["doc_id"])
        if not isinstance(verdict, dict) or not mapping:
            continue
        for field in bs.PDF6_FIELDS:
            entry = (verdict.get("fields") or {}).get(field) or {}
            status = {
                mapping[lab]: st
                for lab, st in (entry.get("status") or {}).items()
                if lab in mapping
            }
            if len(status) < 2:
                continue
            bad = sum(1 for st in status.values() if st in ("unsupported", "missing_but_in_source"))
            ok = sum(1 for st in status.values() if st == "correct")
            key = (len(set(status.values())), min(bad, ok), line.get("source") == "pdf")
            scored.append((key, line["doc_id"], field, status, verdict))
    picked, seen = [], set()
    for key, doc_id, field, status, verdict in sorted(
        scored, key=lambda t: (t[0], -t[1]), reverse=True
    ):
        if doc_id in seen:
            continue
        seen.add(doc_id)
        mapping = labels[doc_id]
        arm_lines = []
        for arm in sorted(status, key=eb.ALL_ARMS.index):
            value = _field_text(outputs.get(doc_id, {}).get(arm, {}), field)
            arm_lines.append(
                f"  {arm}: {_STATUS_SHORT[status[arm]]}, {value[:EXAMPLE_VALUE_CHARS].strip()}"
            )
        reason = str(verdict.get("reasoning") or "")[:EXAMPLE_REASON_CHARS]
        letters = " ".join(f"{label}={arm}" for label, arm in sorted(mapping.items()))
        picked.append(
            f"doc {doc_id}, {field}\n"
            + "\n".join(arm_lines)
            + f"\n  judge: {reason}\n  (the judge's letters: {letters})"
        )
        if len(picked) == count:
            break
    return picked


def render_pdf6_summary(
    run_dir: pathlib.Path, out_dir: pathlib.Path, arms: Optional[Iterable[str]] = None
) -> str:
    """The six-way summary as plain text. Objective figures are over the Sonnet subset (the
    documents every arm ran), not each arm's own documents, so they compare."""
    run_dir, out_dir = pathlib.Path(run_dir), pathlib.Path(out_dir)
    wanted = list(arms) if arms else list(bs.PDF6_ARMS)
    subset = bs.sonnet_doc_ids()
    score = bs.score_run(run_dir, wanted, doc_ids=subset)
    results = bs.load_results(run_dir, wanted)
    names = score["arms_present"]
    judge = bs.summarize_pdf6(out_dir)
    f = _num

    out = [
        f"Six-way PDF judge summary, run {run_dir.name}",
        f"Arms: {', '.join(names)}"
        + (f"  (missing: {', '.join(score['arms_missing'])})" if score["arms_missing"] else ""),
        f"Objective figures over the {len(subset) if subset else 'all'}-document Sonnet subset.",
        "",
        "ARMS: validity and cost",
    ]
    rows = []
    for arm in names:
        a = score["arms"][arm]
        c = a["cost"]
        live, batch = _live_and_batch_per_1000(arm, a)
        billed = c["billed"] or 1
        rows.append(
            [
                arm,
                str(a["model"]),
                a["input"],
                f"{a['valid']}/{a['n']}",
                _pct(a["validity"]),
                f"{c['tokens_in'] / billed:,.0f}",
                f"{c['tokens_out'] / billed:,.0f}",
                _usd(c["usd"], 4),
                _usd(c["usd"] / billed, 4) if c["billed"] else "—",
                _usd(live, 2),
                _usd(batch, 2),
            ]
        )
    out.append(
        _text_table(
            (
                "arm",
                "model",
                "input",
                "valid",
                "validity",
                "tok in/doc",
                "tok out/doc",
                "spent",
                "$/doc",
                "live $/1k",
                "batch-eq $/1k",
            ),
            rows,
        )
    )
    flash = "GF-pdf-low"
    if flash in score["arms"] and score["arms"][flash]["cost"]["billed"]:
        c = score["arms"][flash]["cost"]
        per_1000 = {
            year: (c["tokens_in"] * rate.input_per_token + c["tokens_out"] * rate.output_per_token)
            / c["billed"]
            * 1000
            for year, rate in (
                ("2026 (to 2026-12-31)", eb.GEMINI_FLASH_RATE_2026),
                ("2027", eb.GEMINI_FLASH_RATE_2027),
            )
        }
        out += ["", "gemini-3.8-flash price, same tokens (GF-pdf-low):"]
        out.append(
            _text_table(
                ("price", "live $/1k", "batch-eq $/1k"),
                [
                    [y, _usd(v, 2), _usd(v * eb.pricing.BATCH_DISCOUNT, 2)]
                    for y, v in per_1000.items()
                ],
            )
        )
    out += ["", "Errors (reasons):"]
    out += [f"  {arm}: {_error_reasons(results.get(arm, []))}" for arm in names]

    out += ["", "EMPTY RATE per field (valid outputs)"]
    out.append(
        _text_table(
            ("arm", *bs.FIELDS),
            [
                [arm, *[_pct(score["arms"][arm]["empty_rates"][fld]) for fld in bs.FIELDS]]
                for arm in names
            ],
        )
    )
    out += ["", "APPLICABILITY recall vs NHTSA associatedProducts"]
    out.append(
        _text_table(
            ("arm", "recall", "years", "models", "docs"),
            [
                [
                    arm,
                    _pct(score["arms"][arm]["applicability"]["recall"]),
                    _pct(score["arms"][arm]["applicability"]["year_recall"]),
                    _pct(score["arms"][arm]["applicability"]["model_recall"]),
                    str(score["arms"][arm]["applicability"]["n"]),
                ]
                for arm in names
            ],
        )
    )

    jnames = list(judge["wins"])
    out += [
        "",
        f"JUDGE: {judge['judged']} documents judged blind by {bs.JUDGE_MODEL} reading the PDF "
        f"({judge['errors']} unusable), ${judge['usd']:.2f}",
        "Source per document: "
        + (", ".join(f"{k} {v}" for k, v in sorted(judge["sources"].items())) or "none"),
        "",
        "Overall ranking",
    ]
    out.append(
        _text_table(
            ("arm", "wins", "mean rank", "unsupported claims", "docs with any"),
            [
                [
                    arm,
                    str(judge["wins"][arm]),
                    f(judge["mean_rank"][arm]),
                    str(judge["unsupported_claims"][arm]),
                    str(judge["unsupported_documents"][arm]),
                ]
                for arm in jnames
            ],
        )
    )
    out += ["", "Best per field (judge's pick; tie = no clear best)"]
    out.append(
        _text_table(
            ("field", *jnames, "tie"),
            [
                [
                    fld,
                    *[str(judge["field_best"][fld][a]) for a in jnames],
                    str(judge["field_best"][fld]["tie"]),
                ]
                for fld in bs.PDF6_FIELDS
            ],
        )
    )
    out += [
        "",
        "Status per field and arm: " + " / ".join(f"{_STATUS_SHORT[s]}={s}" for s in _STATUS_ORDER),
    ]
    rows = []
    for fld in bs.PDF6_FIELDS:
        for arm in jnames:
            counts = judge["field_status"][fld].get(arm, {})
            rows.append([fld, arm, *[str(counts.get(s, 0)) for s in _STATUS_ORDER]])
    out.append(_text_table(("field", "arm", *[_STATUS_SHORT[s] for s in _STATUS_ORDER]), rows))

    examples = _pick_examples(run_dir, out_dir, [a for a in jnames if a in names] or names)
    out += ["", "EXAMPLES where the arms differ most"]
    out += examples or ["  (none: no usable verdicts)"]
    return "\n".join(out) + "\n"


def write_pdf6_summary(
    run_dir: pathlib.Path,
    out_dir: Optional[pathlib.Path] = None,
    arms: Optional[Iterable[str]] = None,
) -> pathlib.Path:
    """Write ``summary.md`` into the six-way judge's folder (default ``run_dir/judge-pdf6``)."""
    run_dir = pathlib.Path(run_dir)
    out_dir = pathlib.Path(out_dir) if out_dir else run_dir / bs.PDF6_DIR
    path = out_dir / SUMMARY_NAME
    path.write_text(render_pdf6_summary(run_dir, out_dir, arms))
    return path


# --------------------------------------------------------------------------- entry points


def write_report(run_dir: pathlib.Path, arms: Optional[Iterable[str]] = None) -> list[pathlib.Path]:
    """Write ``report.md`` and ``report.html`` into ``run_dir``; return both paths."""
    run_dir = pathlib.Path(run_dir)
    data = _collect(run_dir, arms)
    md, page = run_dir / "report.md", run_dir / "report.html"
    md.write_text(render_markdown(data))
    page.write_text(render_html(data))
    return [md, page]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.eval.bakeoff_report", description=__doc__)
    p.add_argument(
        "--run-dir",
        type=pathlib.Path,
        help="run directory (default: newest under backend/.bakeoff/)",
    )
    p.add_argument(
        "--arms",
        nargs="+",
        metavar="ARM",
        help=f"arms to report (default: {' '.join(eb.DEFAULT_ARMS)}); commas or spaces",
    )
    p.add_argument(
        "--pdf6",
        action="store_true",
        help=f"write only {bs.PDF6_DIR}/{SUMMARY_NAME} (the six-way PDF judge summary)",
    )
    p.add_argument(
        "--judge-dir",
        type=pathlib.Path,
        help=f"with --pdf6: the judge folder, relative to the run dir (default {bs.PDF6_DIR})",
    )
    args = p.parse_args(argv)
    run_dir = args.run_dir or eb.latest_run_dir()
    try:
        arms = bs.parse_arm_names(args.arms)
        if args.pdf6:
            if run_dir is None or not bs.load_reference(run_dir):
                print("error: reference.json missing; run bakeoff_score first", file=sys.stderr)
                return 2
            out_dir = run_dir / (args.judge_dir or bs.PDF6_DIR)
            print(write_pdf6_summary(run_dir, out_dir, arms))
            return 0
        if run_dir is None or not bs.present_arms(run_dir, arms):
            print("error: no run directory with results.jsonl; pass --run-dir", file=sys.stderr)
            return 2
        if not bs.load_reference(run_dir):
            print("error: reference.json missing; run bakeoff_score first", file=sys.stderr)
            return 2
        for path in write_report(run_dir, arms):
            print(path)
        return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
