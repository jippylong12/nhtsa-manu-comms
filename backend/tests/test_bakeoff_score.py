"""Scoring, blind judge and report for the extraction bake-off (JIP-1568).

Offline: the judge's ``_generate`` is replaced, the ledger write is replaced, and a
guard fails the test if a real Anthropic client is ever constructed.
"""

import json
import pathlib

import anthropic
import pytest

from src.eval import bakeoff_report as report
from src.eval import bakeoff_score as bs


@pytest.fixture(autouse=True)
def _no_real_clients(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("a real provider client was used in a test")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", _boom)
    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", _boom)


@pytest.fixture(autouse=True)
def _isolated_bakeoff_root(tmp_path, monkeypatch):
    """Runs under the real backend/.bakeoff/ never count toward a test's cap."""
    monkeypatch.setattr(bs.eb, "BAKEOFF_ROOT", tmp_path / "bakeoff-root")


# --------------------------------------------------------------------------- S1 metric math


def _out(**kw):
    base = {
        "summary": "Replace the fuel pump",
        "symptoms": ["Engine stalls", "hard start"],
        "systems": ["fuel"],
        "components": ["fuel pump"],
        "remedy": "Replace fuel pump module",
        "applicability": "2015 F-150",
        "doc_kind": "service_procedure",
    }
    base.update(kw)
    return base


def test_jaccard_lowercases_and_strips_and_counts_two_empty_sets_as_agreement():
    assert bs.jaccard(
        ["Engine Stalls ", "hard start"], ["engine stalls", "no crank"]
    ) == pytest.approx(1 / 3)
    assert bs.jaccard([], []) == 1.0
    assert bs.jaccard(["a"], []) == 0.0


def test_token_f1_is_a_bag_of_lowercase_word_tokens():
    # tokens a: replace fuel pump (3), b: replace the fuel pump module (5); overlap 3
    p, r = 3 / 5, 3 / 3
    assert bs.token_f1("Replace fuel pump", "replace the fuel pump module") == pytest.approx(
        2 * p * r / (p + r)
    )
    assert bs.token_f1("", "") == 1.0
    assert bs.token_f1("x", "") == 0.0


def test_compare_outputs_scores_every_field_on_a_handcrafted_pair():
    a = _out()
    b = _out(
        symptoms=["engine stalls"],
        systems=["fuel", "electrical"],
        components=["fuel pump"],
        remedy="Replace fuel pump",
        doc_kind="recall",
        summary="Replace the fuel pump",
    )
    got = bs.compare_outputs(a, b)
    assert got["symptoms"] == pytest.approx(1 / 2)
    assert got["systems"] == pytest.approx(1 / 2)
    assert got["components"] == 1.0
    assert got["doc_kind"] == 0.0
    assert got["summary"] == 1.0
    assert got["remedy"] == pytest.approx(2 * (1.0 * 3 / 4) / (1.0 + 3 / 4))


# --------------------------------------------------------------------------- S2 applicability recall

PRODUCTS = [
    {"type": "Vehicle", "productYear": "2015", "productModel": "F-150"},
    {"type": "Vehicle", "productYear": "2017", "productModel": "MUSTANG MACH-E"},
]


def test_applicability_recall_is_one_when_every_year_and_model_is_named_including_hyphen_variants():
    text = "Applies to 2015 and 2017 Ford F 150 and Mustang Mach E"
    got = bs.applicability_recall(PRODUCTS, text)
    assert got["recall"] == 1.0
    assert got["year_recall"] == 1.0 and got["model_recall"] == 1.0


def test_applicability_recall_is_half_when_half_are_named():
    got = bs.applicability_recall(PRODUCTS, "2015 F-150 only")
    assert got["year_recall"] == 0.5 and got["model_recall"] == 0.5
    assert got["recall"] == 0.5


def test_applicability_recall_is_zero_when_none_are_named_and_none_when_nhtsa_lists_nothing():
    assert bs.applicability_recall(PRODUCTS, "Certain trucks")["recall"] == 0.0
    assert bs.applicability_recall([], "2015 F-150")["recall"] is None
    assert bs.applicability_recall([{"type": "Tire"}], "2015")["recall"] is None


def test_a_year_inside_a_stated_range_counts_and_a_model_must_not_match_inside_a_longer_name():
    products = [
        {"productYear": "2016", "productModel": "F-150"},
        {"productYear": "2016", "productModel": "F-15"},
    ]
    got = bs.applicability_recall(products, "2015-2017 F 150")
    assert got["year_recall"] == 1.0
    assert got["model_recall"] == 0.5


# --------------------------------------------------------------------------- run fixtures

ARMS = ("G", "H-text", "H-pdf")


def _line(arm, doc_id, output, *, error=None, cost=0.001, tin=1000, tout=200):
    return {
        "doc_id": doc_id,
        "nhtsa_id": f"N{doc_id}",
        "arm": arm,
        "model": "m",
        "output": output,
        "valid": output is not None,
        "error": error,
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
        "collected_at": "2026-11-07T10:00:00+00:00",
    }


def _write_arm(run_dir: pathlib.Path, arm, lines):
    d = run_dir / arm
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (d / "batch.json").write_text(
        json.dumps({"arm": arm, "submitted_at": "2026-11-07T09:00:00+00:00", "batches": []})
    )


def _write_reference(run_dir: pathlib.Path, doc_ids, products=None):
    ref = {
        str(i): {
            "nhtsa_id": f"N{i}",
            "nhtsa_summary": f"NHTSA summary {i}",
            "stored": _out(),
            "products": products if products is not None else PRODUCTS,
            "source": f"Source text for document {i}. " * 5,
        }
        for i in doc_ids
    }
    (run_dir / "reference.json").write_text(json.dumps(ref))


# --------------------------------------------------------------------------- S1 + partial arms


def test_score_run_scores_the_arms_that_exist_and_names_the_missing_one(tmp_path):
    good = _out(applicability="2015 and 2017 F-150, Mustang Mach-E")
    off = _out(symptoms=["engine stalls"], remedy="Replace fuel pump", applicability="2015 F-150")
    _write_arm(
        tmp_path,
        "G",
        [
            _line("G", 1, good),
            _line("G", 2, off),
            _line("G", 3, None, error="no extracted text", cost=0.0),
        ],
    )
    _write_arm(
        tmp_path,
        "H-text",
        [
            _line("H-text", 1, good, cost=0.002),
            _line("H-text", 2, None, error="missing fields: remedy", cost=0.002),
            _line("H-text", 3, None, error="no extracted text", cost=0.0),
        ],
    )
    _write_reference(tmp_path, [1, 2, 3])

    score = bs.score_run(tmp_path)

    assert score["arms_present"] == ["G", "H-text"]
    assert score["arms_missing"] == ["H-pdf"]
    assert "H-pdf" not in score["arms"]
    g, h = score["arms"]["G"], score["arms"]["H-text"]
    assert g["validity"] == pytest.approx(2 / 3) and h["validity"] == pytest.approx(1 / 3)
    assert g["empty_rates"]["remedy"] == 0.0
    # against the stored output: doc 1 identical, doc 2 differs on symptoms (1/2) and remedy
    assert g["vs_stored"]["n"] == 2
    assert g["vs_stored"]["symptoms"] == pytest.approx((1.0 + 1 / 2) / 2)
    assert g["vs_stored"]["doc_kind"] == 1.0
    assert h["vs_stored"]["n"] == 1 and h["vs_stored"]["symptoms"] == 1.0
    # applicability recall: doc 1 is 4/4, doc 2 is 2/4 (2015 and F 150 only)
    assert g["applicability"]["recall"] == pytest.approx((1.0 + 0.5) / 2)
    # cost: G $0.002 over 3 results, H-text $0.004 over 3
    assert g["cost"]["usd"] == pytest.approx(0.002)
    assert g["cost"]["usd_per_1000"] == pytest.approx(0.002 / 3 * 1000)
    assert g["cost"]["tokens_in"] == 3000
    assert g["wall_seconds"] == 3600.0
    # pairwise between the arms: only doc 1 has both valid
    assert score["pairwise"]["G|H-text"]["n"] == 1
    assert score["pairwise"]["G|H-text"]["symptoms"] == 1.0
    assert score["disagreement"] == {
        1: 0.0
    }, "only document 1 has two valid outputs, and they agree"
    assert (tmp_path / "score.json").exists() is False, "score_run is pure; the CLI writes"


# --------------------------------------------------------------------------- S3 blind judge

SYMPTOM_BANK = [
    ["engine stalls"],
    ["no crank", "hard start"],
    ["rough idle"],
    ["loud whine"],
    ["check engine light"],
    ["brake pedal soft"],
    ["screen blank"],
    ["door will not latch"],
    ["a c warm"],
    ["steering pull"],
    ["battery drain"],
    ["window stuck"],
]


def _three_arm_run(tmp_path, n=12, arms=ARMS):
    """``n`` documents; arm G always agrees with stored, the others drift more as i grows."""
    for arm_no, arm in enumerate(arms):
        lines = []
        for i in range(1, n + 1):
            if arm == "G":
                out = _out()
            else:
                drift = (
                    SYMPTOM_BANK[(i + arm_no) % len(SYMPTOM_BANK)]
                    if i % 2
                    else ["engine stalls", "hard start"]
                )
                out = _out(
                    symptoms=drift,
                    remedy=f"Remedy variant {arm_no} {i}" if i % 2 else "Replace fuel pump module",
                )
            lines.append(_line(arm, i, out))
        _write_arm(tmp_path, arm, lines)
    _write_reference(tmp_path, range(1, n + 1))


def _verdict(labels):
    order = sorted(labels)
    return {
        "ranking": order,
        "faithfulness": [
            {
                "label": lab,
                "unsupported_claims": (["invented a torque value"] if lab == order[-1] else []),
            }
            for lab in order
        ],
        "completeness": {
            "symptoms": order[0],
            "remedy": order[0],
            "applicability": order[1 % len(order)],
        },
        "reasoning": "A names the fix directly.",
    }


@pytest.fixture
def judge_env(tmp_path, monkeypatch):
    _three_arm_run(tmp_path)
    prompts, usage = [], []

    def fake_generate(prompt):
        prompts.append(prompt)
        labels = sorted(
            set(
                __import__("re").findall(
                    r"^=== OUTPUT ([A-C]) ===$", prompt, flags=__import__("re").M
                )
            )
        )
        return {"verdict": _verdict(labels), "tokens_in": 5000, "tokens_out": 3000}

    async def fake_record_usage(kind, model, **kwargs):
        usage.append({"kind": kind, "model": model, **kwargs})
        return 0.0

    monkeypatch.setattr(bs, "_generate", fake_generate)
    monkeypatch.setattr(bs, "record_usage", fake_record_usage)
    return type("Env", (), {"run_dir": tmp_path, "prompts": prompts, "usage": usage})


def test_labels_are_shuffled_deterministically_and_reversible():
    first = bs.label_arms(7, list(ARMS), seed=20261106)
    assert sorted(first) == ["A", "B", "C"] and sorted(first.values()) == sorted(ARMS)
    assert bs.label_arms(7, list(reversed(ARMS)), seed=20261106) == first
    orders = {tuple(bs.label_arms(d, list(ARMS), seed=20261106).values()) for d in range(1, 40)}
    assert len(orders) > 1, "the assignment varies by document"


def test_the_judge_prompt_has_the_source_and_every_output_and_no_arm_name():
    labels = {"A": "H-pdf", "B": "G", "C": "H-text"}
    outputs = {
        "H-pdf": _out(remedy="remedy zebra"),
        "G": _out(remedy="remedy yak"),
        "H-text": _out(remedy="remedy wombat"),
    }
    prompt = bs.build_judge_prompt("SOURCE-MARKER " + "x" * 20000, labels, outputs)
    for marker in ("zebra", "yak", "wombat", "SOURCE-MARKER"):
        assert marker in prompt
    assert (
        prompt.index("zebra") < prompt.index("yak") < prompt.index("wombat")
    ), "ordered by label A, B, C"
    assert len(prompt) < 12_000 + 5_000, "the source is cut to its first 12,000 characters"
    for name in ("H-pdf", "H-text", "gemini", "haiku", "Haiku", "Gemini"):
        assert name not in prompt


def test_selection_takes_the_most_disagreeing_documents_then_a_seeded_random_rest(tmp_path):
    _three_arm_run(tmp_path)
    by_doc = bs.valid_outputs_by_doc(bs.load_results(tmp_path))
    gaps = bs.score_run(tmp_path)["disagreement"]
    hard, rand = bs.select_judge_docs(by_doc, n_hard=4, n_random=3, seed=20261106)
    assert hard == sorted(gaps, key=lambda d: (-gaps[d], d))[:4]
    assert len(rand) == 3 and not set(rand) & set(hard)
    assert bs.select_judge_docs(by_doc, n_hard=4, n_random=3, seed=20261106) == (hard, rand)


async def test_run_judge_is_blind_records_the_label_map_and_meters_every_call(judge_env):
    out = await bs.run_judge(judge_env.run_dir, n_hard=4, n_random=2)

    assert out["judged"] == 6
    assert len(judge_env.prompts) == 6
    for prompt in judge_env.prompts:
        assert "H-pdf" not in prompt and "H-text" not in prompt
    labels = json.loads((judge_env.run_dir / "judge_labels.json").read_text())
    assert len(labels) == 6
    for mapping in labels.values():
        assert sorted(mapping.values()) == sorted(ARMS)
    lines = [json.loads(x) for x in (judge_env.run_dir / "judge.jsonl").read_text().splitlines()]
    assert len(lines) == 6 and all(x["error"] is None for x in lines)
    assert {x["stratum"] for x in lines} == {"hard", "random"}
    assert len(judge_env.usage) == 6
    u = judge_env.usage[0]
    assert u["model"] == "claude-opus-5-5" and u["batch"] is False
    assert u["meta"]["bakeoff"] == "judge" and u["meta"]["run"] == str(judge_env.run_dir)
    assert u["tokens_in"] == 5000 and u["tokens_out"] == 3000

    summary = bs.summarize_judge(judge_env.run_dir)
    assert sum(summary["wins"].values()) == 6, "labels map back to arm names"
    assert set(summary["wins"]) == set(ARMS)
    assert sum(summary["faithfulness_flags"].values()) == 6


async def test_run_judge_refuses_before_any_call_when_the_cap_would_be_passed(
    judge_env, monkeypatch
):
    monkeypatch.setattr(bs.eb, "CAP_USD", 0.05)

    with pytest.raises(bs.eb.SpendCapExceeded, match="cap"):
        await bs.run_judge(judge_env.run_dir, n_hard=4, n_random=2)

    assert judge_env.prompts == [] and judge_env.usage == []
    assert not (judge_env.run_dir / "judge.jsonl").exists()


async def test_run_judge_works_on_the_arms_that_exist(tmp_path, monkeypatch):
    _three_arm_run(tmp_path, arms=("G", "H-text"))
    seen = []
    monkeypatch.setattr(
        bs,
        "_generate",
        lambda p: seen.append(p)
        or {"verdict": _verdict(["A", "B"]), "tokens_in": 1, "tokens_out": 1},
    )

    async def fake_record_usage(kind, model, **kw):
        return 0.0

    monkeypatch.setattr(bs, "record_usage", fake_record_usage)
    out = await bs.run_judge(tmp_path, n_hard=2, n_random=2)
    assert out["judged"] == 4
    assert all("=== OUTPUT C ===" not in p for p in seen)
    assert list(bs.summarize_judge(tmp_path)["wins"]) == ["G", "H-text"]


# --------------------------------------------------------------------------- S4 report


async def test_report_renders_both_files_with_twenty_documents_and_a_judge_note(
    tmp_path, judge_env
):
    _three_arm_run(tmp_path, n=30)
    await bs.run_judge(tmp_path, n_hard=5, n_random=5)

    paths = report.write_report(tmp_path)

    assert paths == [tmp_path / "report.md", tmp_path / "report.html"]
    md, html = (p.read_text() for p in paths)
    assert html.count('<section class="doc"') == 20
    assert md.count("\n### Document ") == 20
    for arm in ARMS:
        assert arm in html and arm in md
    # a side-by-side document shows NHTSA's summary, the source and the judge's note
    assert "NHTSA summary" in html and "Source text for document" in html
    assert "A names the fix directly." in html and "A names the fix directly." in md
    assert "invented a torque value" in html
    # single file, readable in both themes, nothing fetched from elsewhere
    assert "prefers-color-scheme: dark" in html and "<style>" in html
    assert "<script src" not in html and "<link " not in html and "http://" not in html
    assert "Cost per 1,000 docs" in html and "Wall time" in html
    assert "Missing arms" not in html


def test_report_names_the_arms_that_have_no_results_yet(tmp_path):
    _three_arm_run(tmp_path, n=30, arms=("G", "H-text"))

    paths = report.write_report(tmp_path)

    md, html = (p.read_text() for p in paths)
    assert "Missing arms: H-pdf" in md and "Missing arms: H-pdf" in html
    assert html.count('<section class="doc"') == 20
    assert "Judge has not run" in html


# --------------------------------------------------------------------------- review fixes


async def test_run_judge_stops_before_a_call_whose_worst_case_could_pass_the_cap(
    judge_env, monkeypatch
):
    # Every call thinks to max_tokens: far past the 4,000-token estimate.
    monkeypatch.setattr(
        bs,
        "_generate",
        lambda p: {"verdict": None, "tokens_in": 5000, "tokens_out": bs.JUDGE_MAX_TOKENS},
    )
    arms = bs.eb.run_spend(judge_env.run_dir).total
    monkeypatch.setattr(bs.eb, "CAP_USD", arms + 0.75)

    out = await bs.run_judge(judge_env.run_dir, n_hard=4, n_random=2)

    judge_total = bs.eb.judge_spend(judge_env.run_dir)
    assert out["stopped_at_cap"] is True and out["judged"] == 2
    assert arms + judge_total <= bs.eb.CAP_USD


async def test_run_judge_counts_spend_from_another_run_of_the_bakeoff(judge_env, monkeypatch):
    other = bs.eb.BAKEOFF_ROOT / "20261101-0900"
    _write_arm(other, "G", [_line("G", 1, _out(), cost=9.99)])

    with pytest.raises(bs.eb.SpendCapExceeded, match="cap"):
        await bs.run_judge(judge_env.run_dir, n_hard=4, n_random=2)
    assert judge_env.prompts == []


def test_summarize_ignores_a_faithfulness_label_with_no_output_behind_it(tmp_path):
    _three_arm_run(tmp_path, arms=("G", "H-text"))
    verdict = _verdict(["A", "B"])
    verdict["faithfulness"].append({"label": "C", "unsupported_claims": ["x"]})
    (tmp_path / "judge.jsonl").write_text(json.dumps({"doc_id": 1, "verdict": verdict}) + "\n")
    (tmp_path / "judge_labels.json").write_text(json.dumps({"1": {"A": "G", "B": "H-text"}}))

    summary = bs.summarize_judge(tmp_path)

    assert summary["judged"] == 1
    assert sum(summary["faithfulness_flags"].values()) == 1
