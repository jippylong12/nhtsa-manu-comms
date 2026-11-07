"""Side-by-side report for the extraction bake-off (JIP-1568).

``python -m src.eval.bakeoff_report --run-dir RUN`` writes ``report.md`` and
``report.html`` into the run directory. It reads only files already there
(``results.jsonl`` per arm, ``reference.json``, and ``judge.jsonl`` plus
``judge_labels.json`` when the judge has run), so it needs no database and no key.

The HTML is one file with inline CSS and no script, readable in light and dark.
Arms with no results yet are named in a "Missing arms" line, and the report is
built from the arms that exist.
"""

import argparse
import html
import pathlib
import random
import sys
from typing import Any, Optional

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


def _usd(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.2f}"


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


def _collect(run_dir: pathlib.Path) -> dict[str, Any]:
    score = bs.score_run(run_dir)
    results = bs.load_results(run_dir)
    by_doc = bs.valid_outputs_by_doc(results)
    judge_lines, labels = bs.load_judge(run_dir)
    lines = {x["doc_id"]: x for x in judge_lines}
    reference = bs.load_reference(run_dir)
    docs = []
    for doc_id, why in pick_documents(score["disagreement"]):
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
        "judge": bs.summarize_judge(run_dir),
        "judged": bool(judge_lines),
        "docs": docs,
    }


# --------------------------------------------------------------------------- headline rows

HEADLINE = (
    "Arm",
    "Model",
    "Valid",
    "Symptoms vs stored (Jaccard)",
    "Remedy vs stored (F1)",
    "Applicability recall",
    "Judge wins",
    "Faithfulness flags",
    "Cost per 1,000 docs",
    "Wall time",
)


def _headline_rows(data: dict[str, Any]) -> list[list[str]]:
    rows = []
    judge = data["judge"]
    for arm, a in data["score"]["arms"].items():
        rows.append(
            [
                arm,
                a["model"] or "—",
                f"{_pct(a['validity'])} ({a['valid']}/{a['n']})",
                _num(a["vs_stored"]["symptoms"]),
                _num(a["vs_stored"]["remedy"]),
                _num(a["applicability"]["recall"]),
                str(judge["wins"].get(arm, 0)) if data["judged"] else "—",
                (
                    f"{judge['faithfulness_flags'].get(arm, 0)} in {judge['flagged_documents'].get(arm, 0)} docs"
                    if data["judged"]
                    else "—"
                ),
                _usd(a["cost"]["usd_per_1000"]),
                _duration(a["wall_seconds"]),
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
    if _missing_line(score):
        out += [f"**{_missing_line(score)}**", ""]
    out += ["## Headline", "", *_md_table(HEADLINE, _headline_rows(data)), ""]
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
        for arm, output in d["outputs"].items():
            out += [f"**{arm}**", ""]
            out += [f"- {f}: {_field_text(output, f)}" for f in bs.FIELDS]
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
:root{--bg:#fbfbfa;--fg:#1c1c1e;--muted:#63636b;--card:#fff;--line:#e2e2e0;--accent:#2f5fd0;--warn:#b4521e;--chip:#eef1fb}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#e9e9ec;--muted:#9a9aa4;--card:#1d1d20;--line:#34343a;--accent:#8fb0ff;--warn:#f0a070;--chip:#262a3a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 64px}
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
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:10px}
.col{border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0}
.col h4{margin:0 0 6px;font-size:.9rem;color:var(--accent)}
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


def _html_doc(d: dict[str, Any]) -> str:
    cols = []
    for arm, output in d["outputs"].items():
        fields = "".join(
            f"<dt>{_h(f)}</dt><dd>{_h(_field_text(output, f))}</dd>" for f in bs.FIELDS
        )
        cols.append(f'<div class="col"><h4>{_h(arm)}</h4><dl>{fields}</dl></div>')
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
    missing = _missing_line(score)
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
        "<h2>Headline</h2>",
        _html_table(HEADLINE, _headline_rows(data)),
        "<h2>Agreement</h2>",
        _html_table(_PAIR_HEAD, _pairwise_rows(score)),
        "<h2>Empty fields</h2>",
        _html_table(_EMPTY_HEAD, _empty_rows(score)),
        "<h2>Judge</h2>",
        judge_html,
        f"<h2>Side by side ({len(data['docs'])} documents)</h2>",
        *(_html_doc(d) for d in data["docs"]),
        "</main></body></html>",
    ]
    return "".join(parts)


# --------------------------------------------------------------------------- entry points


def write_report(run_dir: pathlib.Path) -> list[pathlib.Path]:
    """Write ``report.md`` and ``report.html`` into ``run_dir``; return both paths."""
    run_dir = pathlib.Path(run_dir)
    data = _collect(run_dir)
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
    args = p.parse_args(argv)
    run_dir = args.run_dir or eb.latest_run_dir()
    if run_dir is None or not bs.present_arms(run_dir):
        print("error: no run directory with results.jsonl; pass --run-dir", file=sys.stderr)
        return 2
    if not bs.load_reference(run_dir):
        print("error: reference.json missing; run bakeoff_score first", file=sys.stderr)
        return 2
    for path in write_report(run_dir):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
