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
from src.eval import extraction_bakeoff as eb


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
    # No Sonnet subset file unless a test writes one: the judge and report use every document.
    monkeypatch.setattr(bs.eb, "SONNET_SAMPLE_PATH", tmp_path / "no-sonnet-sample.json")


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

ARMS = ("G", "H-text-live", "H-pdf-live")
FIVE = eb.DEFAULT_ARMS


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
    if eb.arm_mode(arm) == "live":
        (d / "live.json").write_text(
            json.dumps(
                {
                    "arm": arm,
                    "started_at": "2026-11-07T09:00:00+00:00",
                    "finished_at": "2026-11-07T09:10:00+00:00",
                    "wall_seconds": 600.0,
                }
            )
        )
    else:
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


def test_results_with_a_next_line_character_inside_an_output_field_still_load(tmp_path):
    # json.dumps(ensure_ascii=False), as the harness writes, leaves U+0085 raw in a string.
    out = _out(summary="Step one\x85step two\u2028done")
    d = tmp_path / "G"
    d.mkdir()
    rows = [_line("G", 1, out), _line("G", 2, _out())]
    (d / "results.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)
    )

    loaded = bs.load_results(tmp_path)

    assert [r["doc_id"] for r in loaded["G"]] == [1, 2]
    assert loaded["G"][0]["output"]["summary"] == out["summary"]


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
        "H-text-live",
        [
            _line("H-text-live", 1, good, cost=0.002),
            _line("H-text-live", 2, None, error="missing fields: remedy", cost=0.002),
            _line("H-text-live", 3, None, error="no extracted text", cost=0.0),
        ],
    )
    _write_reference(tmp_path, [1, 2, 3])

    score = bs.score_run(tmp_path)

    assert score["arms_present"] == ["G", "H-text-live"]
    assert score["arms_missing"] == ["H-pdf-live", "S-text", "S-pdf"]
    assert "H-pdf-live" not in score["arms"]
    g, h = score["arms"]["G"], score["arms"]["H-text-live"]
    assert g["validity"] == pytest.approx(2 / 3) and h["validity"] == pytest.approx(1 / 3)
    assert g["empty_rates"]["remedy"] == 0.0
    # against the stored output: doc 1 identical, doc 2 differs on symptoms (1/2) and remedy
    assert g["vs_stored"]["n"] == 2
    assert g["vs_stored"]["symptoms"] == pytest.approx((1.0 + 1 / 2) / 2)
    assert g["vs_stored"]["doc_kind"] == 1.0
    assert h["vs_stored"]["n"] == 1 and h["vs_stored"]["symptoms"] == 1.0
    # applicability recall: doc 1 is 4/4, doc 2 is 2/4 (2015 and F 150 only)
    assert g["applicability"]["recall"] == pytest.approx((1.0 + 0.5) / 2)
    # cost: G $0.002 over 3 results, 2 of them billed; per 1,000 uses the billed count
    assert g["cost"]["usd"] == pytest.approx(0.002)
    assert g["cost"]["billed"] == 2 and h["cost"]["billed"] == 2
    assert g["cost"]["usd_per_1000"] == pytest.approx(0.002 / 2 * 1000)
    assert g["cost"]["tokens_in"] == 3000
    # G ran as a batch, so its cost already is the batch price; a live arm's is halved to match
    assert (g["mode"], h["mode"]) == ("batch", "live")
    assert g["cost"]["usd_per_1000_batch_equivalent"] == pytest.approx(1.0)
    assert h["cost"]["usd_per_1000"] == pytest.approx(0.004 / 2 * 1000)
    assert h["cost"]["usd_per_1000_batch_equivalent"] == pytest.approx(1.0)
    # wall time: submitted to collected for a batch arm, live.json for a live arm
    assert g["wall_seconds"] == 3600.0 and h["wall_seconds"] == 600.0
    # pairwise between the arms: only doc 1 has both valid
    assert score["pairwise"]["G|H-text-live"]["n"] == 1
    assert score["pairwise"]["G|H-text-live"]["symptoms"] == 1.0
    assert score["disagreement"] == {
        1: 0.0
    }, "only document 1 has two valid outputs, and they agree"
    assert (tmp_path / "score.json").exists() is False, "score_run is pure; the CLI writes"


def test_score_run_takes_an_arm_list_and_names_the_canceled_batch_arms_as_superseded(tmp_path):
    _three_arm_run(tmp_path, n=4, arms=(*FIVE, "H-text", "H-pdf"))
    # the canceled batch arms: every line an unbilled error
    for arm in ("H-text", "H-pdf"):
        _write_arm(
            tmp_path,
            arm,
            [_line(arm, i, None, error="batch canceled", cost=0.0, tin=0, tout=0) for i in (1, 2)],
        )

    default = bs.score_run(tmp_path)
    chosen = bs.score_run(tmp_path, arms=["G", "S-text"])
    old = bs.score_run(tmp_path, arms=["G", "H-text", "H-pdf"])

    assert default["arms_present"] == list(FIVE)
    assert default["arms_missing"] == []
    assert default["arms_superseded"] == ["H-text", "H-pdf"]
    assert "H-text" not in default["arms"]
    assert chosen["arms_present"] == ["G", "S-text"] and list(chosen["pairwise"]) == ["G|S-text"]
    assert chosen["arms_superseded"] == ["H-text", "H-pdf"]
    assert old["arms_present"] == ["G", "H-text", "H-pdf"] and old["arms_superseded"] == []
    assert old["arms"]["H-text"]["valid"] == 0 and old["arms"]["H-text"]["cost"]["billed"] == 0
    assert old["arms"]["H-text"]["cost"]["usd_per_1000"] is None


def test_an_unknown_arm_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown arm"):
        bs.score_run(tmp_path, arms=["G", "nope"])


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
                    r"^=== OUTPUT ([A-E]) ===$", prompt, flags=__import__("re").M
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


def test_five_arms_get_five_shuffled_labels_and_the_mapping_restores_every_arm_name():
    first = bs.label_arms(7, list(FIVE), seed=20261106)

    assert sorted(first) == list("ABCDE") and sorted(first.values()) == sorted(FIVE)
    assert bs.label_arms(7, list(reversed(FIVE)), seed=20261106) == first
    assert len({tuple(bs.label_arms(d, list(FIVE), seed=20261106).values()) for d in range(40)}) > 5
    # the verdict schema ranks and credits up to five labels
    schema = bs.JUDGE_SCHEMA["properties"]
    assert schema["ranking"]["items"]["enum"] == list("ABCDE")
    assert schema["completeness"]["properties"]["remedy"]["enum"] == list("ABCDE")


def test_the_judge_prompt_has_the_source_and_every_output_and_no_arm_name():
    labels = {"A": "H-pdf-live", "B": "G", "C": "H-text-live"}
    outputs = {
        "H-pdf-live": _out(remedy="remedy zebra"),
        "G": _out(remedy="remedy yak"),
        "H-text-live": _out(remedy="remedy wombat"),
    }
    prompt = bs.build_judge_prompt("SOURCE-MARKER " + "x" * 20000, labels, outputs)
    for marker in ("zebra", "yak", "wombat", "SOURCE-MARKER"):
        assert marker in prompt
    assert (
        prompt.index("zebra") < prompt.index("yak") < prompt.index("wombat")
    ), "ordered by label A, B, C"
    assert len(prompt) < 12_000 + 5_000, "the source is cut to its first 12,000 characters"
    for name in ("H-pdf-live", "H-text-live", "gemini", "haiku", "Haiku", "Gemini"):
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
        assert "H-pdf-live" not in prompt and "H-text-live" not in prompt
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
    _three_arm_run(tmp_path, arms=("G", "H-text-live"))
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
    assert list(bs.summarize_judge(tmp_path)["wins"]) == ["G", "H-text-live"]


# --------------------------------------------------------------------------- S4 report


async def test_report_renders_five_arms_twenty_sonnet_subset_documents_and_a_judge_note(
    tmp_path, monkeypatch
):
    import re

    _three_arm_run(tmp_path, n=40, arms=(*FIVE, "H-text", "H-pdf"))
    for arm in ("H-text", "H-pdf"):  # the canceled batch arms
        _write_arm(
            tmp_path,
            arm,
            [_line(arm, i, None, error="batch canceled", cost=0.0) for i in range(1, 41)],
        )
    subset = list(range(11, 41))
    path = tmp_path / "sonnet.json"
    path.write_text(json.dumps([{"doc_id": d} for d in subset]))
    monkeypatch.setattr(bs.eb, "SONNET_SAMPLE_PATH", path)
    monkeypatch.setattr(
        bs,
        "_generate",
        lambda p: {"verdict": _verdict(list("ABCDE")), "tokens_in": 5, "tokens_out": 5},
    )

    async def fake_record_usage(kind, model, **kw):
        return 0.0

    monkeypatch.setattr(bs, "record_usage", fake_record_usage)
    await bs.run_judge(tmp_path, n_hard=5, n_random=5)

    paths = report.write_report(tmp_path)

    assert paths == [tmp_path / "report.md", tmp_path / "report.html"]
    md, html = (p.read_text() for p in paths)
    assert html.count('<section class="doc"') == 20
    assert md.count("\n### Document ") == 20
    shown = {int(x) for x in re.findall(r'<section class="doc" id="doc-(\d+)"', html)}
    assert len(shown) == 20 and shown <= set(subset), "drawn from the Sonnet subset"
    assert html.count('<div class="col"') == 100, "all five outputs beside each other"
    # the headline: one row per arm in this order, with the columns the comparison needs
    table = html.split("<h2>Headline</h2>")[1].split("</table>")[0]
    positions = [table.index(f"<td>{arm}</td>") for arm in FIVE]
    assert positions == sorted(positions)
    assert "<td>H-text</td>" not in table and "<td>H-pdf</td>" not in table
    for head in (
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
    ):
        assert f"<th>{head}</th>" in table, head
    assert "<td>batch</td>" in table and table.count("<td>live</td>") == 4
    # G was a batch at half price; the rest ran live at list price
    for doc in (md, html):
        assert "Batch API at half price" in doc and "list price" in doc
        assert "Superseded arms: H-text, H-pdf (batch canceled, superseded by live re-run)" in doc
        assert "Missing arms" not in doc
    # a side-by-side document shows NHTSA's summary, the source and the judge's note
    assert "NHTSA summary" in html and "Source text for document" in html
    assert "A names the fix directly." in html and "A names the fix directly." in md
    assert "invented a torque value" in html
    # one file, light and dark, a grid that wraps, nothing fetched from elsewhere
    assert "prefers-color-scheme: dark" in html and "<style>" in html
    assert "repeat(auto-fit,minmax(" in html
    assert "<script src" not in html and "<link " not in html and "http://" not in html


def test_report_names_the_arms_that_have_no_results_yet(tmp_path):
    _three_arm_run(tmp_path, n=30, arms=("G", "H-text-live"))

    paths = report.write_report(tmp_path)

    md, html = (p.read_text() for p in paths)
    missing = "Missing arms: H-pdf-live, S-text, S-pdf"
    assert missing in md and missing in html
    assert html.count('<section class="doc"') == 20
    assert "Judge has not run" in html


def test_a_document_an_arm_has_no_valid_output_for_still_gets_its_column(tmp_path):
    _three_arm_run(tmp_path, n=10, arms=FIVE)
    _write_arm(
        tmp_path,
        "S-pdf",
        [_line("S-pdf", i, None, error="pdf unavailable", cost=0.0) for i in range(1, 11)],
    )

    html = report.write_report(tmp_path)[1].read_text()

    assert html.count('<div class="col none"') == 10 and html.count('<div class="col"') == 40
    assert "no valid output" in html


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
    _write_arm(other, "G", [_line("G", 1, _out(), cost=bs.eb.CAP_USD - 0.01)])

    with pytest.raises(bs.eb.SpendCapExceeded, match="cap"):
        await bs.run_judge(judge_env.run_dir, n_hard=4, n_random=2)
    assert judge_env.prompts == []


def test_summarize_ignores_a_faithfulness_label_with_no_output_behind_it(tmp_path):
    _three_arm_run(tmp_path, arms=("G", "H-text-live"))
    verdict = _verdict(["A", "B"])
    verdict["faithfulness"].append({"label": "C", "unsupported_claims": ["x"]})
    (tmp_path / "judge.jsonl").write_text(json.dumps({"doc_id": 1, "verdict": verdict}) + "\n")
    (tmp_path / "judge_labels.json").write_text(json.dumps({"1": {"A": "G", "B": "H-text-live"}}))

    summary = bs.summarize_judge(tmp_path)

    assert summary["judged"] == 1
    assert sum(summary["faithfulness_flags"].values()) == 1


# --------------------------------------------------------------------------- five arms and the Sonnet subset


async def test_the_judge_blinds_five_arms_and_summarizes_all_of_them(tmp_path, monkeypatch):
    _three_arm_run(tmp_path, arms=FIVE)
    prompts = []

    def fake_generate(prompt):
        import re

        prompts.append(prompt)
        labels = sorted(set(re.findall(r"^=== OUTPUT ([A-E]) ===$", prompt, flags=re.M)))
        return {"verdict": _verdict(labels), "tokens_in": 5000, "tokens_out": 3000}

    async def fake_record_usage(kind, model, **kw):
        return 0.0

    monkeypatch.setattr(bs, "_generate", fake_generate)
    monkeypatch.setattr(bs, "record_usage", fake_record_usage)

    out = await bs.run_judge(tmp_path, n_hard=4, n_random=2)

    assert out["judged"] == 6
    for prompt in prompts:
        assert "=== OUTPUT E ===" in prompt
        for name in FIVE[1:]:
            assert name not in prompt
    labels = json.loads((tmp_path / "judge_labels.json").read_text())
    assert all(
        sorted(m) == list("ABCDE") and sorted(m.values()) == sorted(FIVE) for m in labels.values()
    )
    summary = bs.summarize_judge(tmp_path)
    assert list(summary["wins"]) == list(FIVE) and sum(summary["wins"].values()) == 6
    assert all(summary["mean_rank"][a] is not None for a in FIVE)
    assert sum(summary["faithfulness_flags"].values()) == 6


async def test_the_judge_only_draws_from_the_sonnet_subset(tmp_path, monkeypatch):
    _three_arm_run(tmp_path, n=20, arms=FIVE)
    subset = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    path = tmp_path / "sonnet.json"
    path.write_text(json.dumps([{"doc_id": d} for d in subset]))
    monkeypatch.setattr(bs.eb, "SONNET_SAMPLE_PATH", path)
    monkeypatch.setattr(
        bs,
        "_generate",
        lambda p: {"verdict": _verdict(list("ABCDE")), "tokens_in": 1, "tokens_out": 1},
    )

    async def fake_record_usage(kind, model, **kw):
        return 0.0

    monkeypatch.setattr(bs, "record_usage", fake_record_usage)

    out = await bs.run_judge(tmp_path, n_hard=3, n_random=3)

    assert out["judged"] == 6
    lines = [json.loads(x) for x in (tmp_path / "judge.jsonl").read_text().splitlines()]
    assert {x["doc_id"] for x in lines} <= set(subset)
    assert {x["stratum"] for x in lines} == {"hard", "random"}


def test_the_judge_estimate_is_shown_before_anything_is_sent(tmp_path, monkeypatch, capsys):
    _three_arm_run(tmp_path, n=12, arms=FIVE)
    monkeypatch.setattr(
        bs, "_generate", lambda p: pytest.fail("--estimate must not call the judge")
    )

    code = bs.main(
        ["--run-dir", str(tmp_path), "--judge", "--estimate", "--n-hard", "4", "--n-random", "2"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "judge: 6 call(s)" in out and "estimate $" in out and "worst case $" in out
    assert f"cap ${bs.eb.CAP_USD:.2f}" in out
    assert not (tmp_path / "judge.jsonl").exists()


def test_the_scorer_and_report_commands_take_an_arms_option(tmp_path, capsys):
    _three_arm_run(tmp_path, n=6, arms=FIVE)

    assert bs.main(["--run-dir", str(tmp_path), "--arms", "G,S-text"]) == 0
    out = capsys.readouterr().out
    assert "arms scored: G, S-text" in out
    assert json.loads((tmp_path / "score.json").read_text())["arms_present"] == ["G", "S-text"]

    assert report.main(["--run-dir", str(tmp_path), "--arms", "G", "S-text"]) == 0
    assert "S-pdf" not in (tmp_path / "report.md").read_text().split("## Judge")[0]


# --------------------------------------------------------------------------- six-way PDF judge

import base64  # noqa: E402
import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

SIX = bs.PDF6_ARMS
NAMED = tuple(a for a in SIX if a != "G")  # "G" is a letter that appears in any prompt
PDF = base64.standard_b64encode(b"%PDF-1.4 judge me").decode()


def _verdict6(labels):
    order = sorted(labels)
    status = {lab: "partial" for lab in order}
    status[order[0]] = "correct"
    status[order[-1]] = "unsupported"
    return {
        "fields": {f: {"best": order[0], "status": dict(status)} for f in bs.PDF6_FIELDS},
        "ranking": order,
        "unsupported_claims": [
            {"label": lab, "claims": (["invented a torque value"] if lab == order[-1] else [])}
            for lab in order
        ],
        "reasoning": "A states the fix directly.",
    }


def _labels_in(content):
    text = content if isinstance(content, str) else content[-1]["text"]
    return sorted(set(re.findall(r"^=== OUTPUT ([A-F]) ===$", text, flags=re.M)))


@pytest.fixture
def pdf6_env(tmp_path, monkeypatch):
    """Six arms over 20 documents, a sample file with urls and pages, a faked judge and PDFs."""
    run = tmp_path / "run"
    run.mkdir()
    _three_arm_run(run, n=20, arms=SIX)
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            [
                {"doc_id": i, "url": f"https://static.nhtsa.gov/{i}.pdf", "page_count": 3}
                for i in range(1, 21)
            ]
        )
    )
    monkeypatch.setattr(bs.eb, "SAMPLE_PATH", sample)
    calls, usage = [], []

    def fake_generate(content, schema):
        calls.append({"content": content, "schema": schema})
        return {"verdict": _verdict6(_labels_in(content)), "tokens_in": 9000, "tokens_out": 3000}

    async def fake_record_usage(kind, model, **kw):
        usage.append({"kind": kind, "model": model, **kw})
        return 0.0

    async def fake_pdfs(entries):
        return {e["doc_id"]: (PDF, None) for e in entries}

    monkeypatch.setattr(bs, "_generate_pdf6", fake_generate)
    monkeypatch.setattr(bs, "record_usage", fake_record_usage)
    monkeypatch.setattr(bs.eb, "_fetch_pdfs", fake_pdfs)
    return SimpleNamespace(
        run_dir=run, calls=calls, usage=usage, sample=sample, monkeypatch=monkeypatch
    )


def _lines(path):
    return [json.loads(x) for x in path.read_text().split("\n") if x.strip()]


def test_six_arms_get_six_shuffled_labels_and_the_mapping_restores_every_arm_name():
    mapping = bs.label_arms(7, list(SIX), labels=bs.LABELS6)

    assert sorted(mapping) == list("ABCDEF") and sorted(mapping.values()) == sorted(SIX)
    assert mapping == bs.label_arms(7, list(reversed(SIX)), labels=bs.LABELS6)
    ranking = list("FEDCBA")
    assert [mapping[x] for x in ranking] == [mapping[x] for x in reversed("ABCDEF")]
    assert (
        len({tuple(bs.label_arms(d, list(SIX), labels=bs.LABELS6).values()) for d in range(40)})
        > 20
    )
    with pytest.raises(ValueError, match="labels"):
        bs.label_arms(1, list(SIX))  # the five-way alphabet cannot name six arms


def test_the_six_way_prompt_blinds_the_arms_and_says_which_source_it_has():
    mapping = bs.label_arms(3, list(SIX), labels=bs.LABELS6)
    outputs = {arm: _out(summary=f"summary from {i}") for i, arm in enumerate(SIX)}

    pdf = bs.build_pdf6_prompt(mapping, outputs)
    text = bs.build_pdf6_prompt(mapping, outputs, excerpt="the bulletin says x")

    for prompt in (pdf, text):
        assert "=== OUTPUT F ===" in prompt
        assert not any(arm in prompt for arm in NAMED)
    assert "attached" in pdf and "=== SOURCE ===" not in pdf
    assert "=== SOURCE ===" in text and "the bulletin says x" in text


def test_the_six_way_schema_asks_a_best_label_and_a_status_per_arm_for_every_field():
    schema = bs.pdf6_schema("ABCD")

    assert schema["additionalProperties"] is False
    assert list(schema["properties"]["fields"]["properties"]) == list(bs.PDF6_FIELDS)
    field = schema["properties"]["fields"]["properties"]["remedy"]
    assert field["properties"]["best"]["enum"] == ["A", "B", "C", "D", "tie"]
    assert sorted(field["properties"]["status"]["properties"]) == list("ABCD")
    assert field["properties"]["status"]["properties"]["A"]["enum"] == [
        "correct",
        "partial",
        "missing_but_in_source",
        "correctly_empty",
        "unsupported",
    ]
    assert {"ranking", "unsupported_claims", "reasoning"} <= set(schema["properties"])

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    assert all(o["additionalProperties"] is False for o in objects(schema))


def test_only_documents_with_four_valid_arms_are_judged_and_the_split_is_hard_plus_random(
    pdf6_env,
):
    # Documents 1-4 are valid in only three arms.
    for arm in SIX[:3]:
        rows = _lines(pdf6_env.run_dir / arm / "results.jsonl")
        for r in rows:
            if r["doc_id"] <= 4:
                r.update(output=None, valid=False, error="pdf unavailable")
        _write_arm(pdf6_env.run_dir, arm, rows)

    plan = bs.plan_pdf6(pdf6_env.run_dir, n_hard=6, n_random=4)

    ids = [j.doc_id for j in plan.jobs]
    assert len(ids) == 10 and all(i > 4 for i in ids)
    assert sorted(j.stratum for j in plan.jobs).count("hard") == 6
    assert all(len(j.labels) == 6 for j in plan.jobs)
    assert plan.out_dir == pdf6_env.run_dir / "judge-pdf6"


async def test_the_judge_reads_the_pdf_blind_and_writes_only_into_its_own_folder(pdf6_env):
    run = pdf6_env.run_dir
    # The five-way judge already covered every document the six-way judge will pick.
    picked = [j.doc_id for j in bs.plan_pdf6(run, n_hard=6, n_random=4).jobs]
    five = "".join(
        json.dumps({"doc_id": d, "verdict": None, "error": "x", "cost_usd": 0.1}) + "\n"
        for d in picked
    )
    (run / "judge.jsonl").write_text(five)
    (run / "judge_labels.json").write_text('{"5": {"A": "G"}}')

    out = await bs.run_judge_pdf6(run, n_hard=6, n_random=4)

    assert out["judged"] == 10 and out["skipped"] == 0, "five-way docs are judged again"
    assert (run / "judge.jsonl").read_text() == five, "the five-way files are untouched"
    assert (run / "judge_labels.json").read_text() == '{"5": {"A": "G"}}'
    folder = run / "judge-pdf6"
    lines = _lines(folder / "judge.jsonl")
    assert len(lines) == 10 and all(x["error"] is None and x["source"] == "pdf" for x in lines)
    assert {x["doc_id"] for x in lines} == set(picked)
    labels = json.loads((folder / "judge_labels.json").read_text())
    assert len(labels) == 10
    assert all(sorted(m) == list("ABCDEF") for m in labels.values())
    for call in pdf6_env.calls:
        block, text = call["content"]
        assert block["type"] == "document" and block["source"]["data"] == PDF
        assert block["source"]["media_type"] == "application/pdf"
        assert not any(arm in text["text"] for arm in NAMED)
        assert sorted(call["schema"]["properties"]["fields"]["properties"]) == sorted(
            bs.PDF6_FIELDS
        )
    [first, *_] = pdf6_env.usage
    assert first["model"] == "claude-opus-5-5" and first["batch"] is False
    assert first["meta"]["bakeoff"] == "judge-pdf6" and len(pdf6_env.usage) == 10


async def test_a_rerun_skips_documents_already_in_the_six_way_file_only(pdf6_env):
    await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=3, n_random=3)
    before = len(pdf6_env.calls)

    again = await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=3, n_random=3)

    assert again["judged"] == 0 and again["skipped"] == 6 and len(pdf6_env.calls) == before


async def test_the_excerpt_is_the_fallback_and_each_line_says_which_source_was_used(pdf6_env):
    async def pdfs(entries):
        out = {}
        for e in entries:
            out[e["doc_id"]] = (
                (None, "pdf too large (30000000 bytes > 24117248)")
                if e["doc_id"] % 2
                else (PDF, None)
            )
        return out

    pdf6_env.monkeypatch.setattr(bs.eb, "_fetch_pdfs", pdfs)
    pdf6_env.sample.write_text(
        json.dumps(
            [
                {"doc_id": i, "url": f"u{i}", "page_count": 601 if i == 20 else 3}
                for i in range(1, 21)
            ]
        )
    )

    await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=20, n_random=0)

    lines = {x["doc_id"]: x for x in _lines(pdf6_env.run_dir / "judge-pdf6" / "judge.jsonl")}
    assert lines[3]["source"] == "excerpt" and "too large" in lines[3]["source_reason"]
    assert lines[4]["source"] == "pdf" and lines[4]["source_reason"] is None
    assert lines[20]["source"] == "excerpt" and "too many pages" in lines[20]["source_reason"]
    excerpt_call = next(c for c in pdf6_env.calls if isinstance(c["content"], str))
    assert (
        "=== SOURCE ===" in excerpt_call["content"]
        and "Source text for document" in excerpt_call["content"]
    )
    summary = bs.summarize_pdf6(pdf6_env.run_dir / "judge-pdf6")
    assert summary["sources"] == {"pdf": 9, "excerpt": 11}


async def test_a_pdf_the_api_rejects_is_retried_once_on_the_excerpt(pdf6_env):
    import httpx

    def reject_pdfs(content, schema):
        if not isinstance(content, str):
            raise anthropic.BadRequestError(
                "document too long",
                response=httpx.Response(400, request=httpx.Request("POST", "https://x.test")),
                body=None,
            )
        return {"verdict": _verdict6(_labels_in(content)), "tokens_in": 100, "tokens_out": 50}

    pdf6_env.monkeypatch.setattr(bs, "_generate_pdf6", reject_pdfs)

    await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=2, n_random=0)

    lines = _lines(pdf6_env.run_dir / "judge-pdf6" / "judge.jsonl")
    assert all(x["source"] == "excerpt" and "pdf rejected" in x["source_reason"] for x in lines)
    assert all(x["error"] is None for x in lines)
    assert len(pdf6_env.usage) == 2, "only the call that was answered is metered"


def test_the_judge_call_uses_the_existing_judge_model_and_settings_with_a_document_block(
    monkeypatch,
):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"ranking": ["A"]}))],
        )

    import src.anthropic_client as ac

    monkeypatch.setattr(
        ac, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    schema = bs.pdf6_schema("ABCD")
    content = [bs._pdf_block(PDF), {"type": "text", "text": "judge"}]

    got = bs._generate_pdf6(content, schema)

    assert seen["model"] == bs.JUDGE_MODEL == "claude-opus-5-5"
    assert seen["max_tokens"] == 16_000 and seen["thinking"] == {"type": "adaptive"}
    assert seen["output_config"]["effort"] == "high"
    assert seen["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert seen["messages"][0]["content"][0]["type"] == "document"
    assert got["verdict"] == {"ranking": ["A"]} and (got["tokens_in"], got["tokens_out"]) == (11, 7)


async def test_the_judge_refuses_when_pairwise_and_probe_spend_leave_no_room(pdf6_env):
    run = bs.eb.BAKEOFF_ROOT / "20261106-2117"
    (run / "pairwise-G-vs-Hpdf").mkdir(parents=True)
    (run / "pairwise-G-vs-Hpdf" / "verdicts.jsonl").write_text(
        json.dumps({"doc_id": 1, "cost_usd": bs.eb.CAP_USD - 0.01}) + "\n"
    )
    (run / "probe-2027").mkdir()
    (run / "probe-2027" / "results.json").write_text(json.dumps({"A": {"cost_true": 0.009}}))

    with pytest.raises(bs.eb.SpendCapExceeded, match="cap"):
        await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=2, n_random=2)

    assert pdf6_env.calls == [] and not (pdf6_env.run_dir / "judge-pdf6" / "judge.jsonl").exists()


async def test_the_six_way_judge_stops_before_a_call_whose_worst_case_would_pass_the_cap(pdf6_env):
    plan = bs.plan_pdf6(pdf6_env.run_dir, n_hard=3, n_random=0)
    assert plan.worst_case > plan.estimate > 0
    # Room for the estimate and the first worst case, not for a second one after the first
    # call has been paid for.
    pdf6_env.monkeypatch.setattr(bs.eb, "CAP_USD", plan.spent + plan.estimate + 0.001)

    out = await bs.run_judge_pdf6(pdf6_env.run_dir, n_hard=3, n_random=0)

    assert out["stopped_at_cap"] is True and len(pdf6_env.calls) == 1, "the second would not fit"


def test_a_verdict_that_misses_a_label_or_a_field_is_unusable_not_a_crash():
    labels = {"A": "G", "B": "S-pdf", "C": "H-pdf-live", "D": "GL-pdf-high"}
    good = _verdict6(list(labels))

    assert bs._check_pdf6_verdict(good, labels) is None
    short = {**good, "ranking": ["A", "B", "C"]}
    assert "not a ranking" in bs._check_pdf6_verdict(short, labels)
    no_field = {**good, "fields": {k: v for k, v in good["fields"].items() if k != "remedy"}}
    assert "remedy" in bs._check_pdf6_verdict(no_field, labels)
    bad_status = json.loads(json.dumps(good))
    bad_status["fields"]["summary"]["status"]["A"] = "perfect"
    assert "unknown status" in bs._check_pdf6_verdict(bad_status, labels)
    assert bs._check_pdf6_verdict(None, labels) == "no verdict"


def test_the_summary_counts_wins_ranks_best_status_and_claims_and_survives_a_ragged_verdict(
    tmp_path,
):
    folder = tmp_path / "judge-pdf6"
    folder.mkdir()
    labels = {"1": {"A": "G", "B": "S-pdf", "C": "H-pdf-live", "D": "GL-pdf-high"}}
    labels["2"] = {"A": "S-pdf", "B": "G", "C": "GL-pdf-high", "D": "H-pdf-live"}
    labels["3"] = {"A": "G", "B": "S-pdf", "C": "H-pdf-live", "D": "GL-pdf-high"}
    (folder / "judge_labels.json").write_text(json.dumps(labels))
    ragged = {
        "ranking": ["Z", "B"],
        "fields": {"remedy": {"best": "tie", "status": {"Q": "correct"}}},
    }
    rows = [
        {"doc_id": 1, "verdict": _verdict6("ABCD"), "source": "pdf", "cost_usd": 1.0},
        {"doc_id": 2, "verdict": _verdict6("ABCD"), "source": "excerpt", "cost_usd": 0.5},
        {"doc_id": 3, "verdict": ragged, "source": "pdf", "cost_usd": 0.25},
        {"doc_id": 4, "verdict": None, "error": "boom", "source": "pdf", "cost_usd": 0.0},
    ]
    (folder / "judge.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    s = bs.summarize_pdf6(folder)

    assert s["judged"] == 3 and s["errors"] == 1 and s["sources"] == {"pdf": 3, "excerpt": 1}
    assert s["wins"]["G"] == 1 and s["wins"]["S-pdf"] == 2, "the ragged verdict still ranks B"
    assert s["mean_rank"]["G"] == pytest.approx((1 + 2) / 2)
    assert s["mean_rank"]["S-pdf"] == pytest.approx((2 + 1 + 1) / 3)
    assert s["field_best"]["remedy"]["G"] == 1 and s["field_best"]["remedy"]["S-pdf"] == 1
    assert s["field_best"]["remedy"]["tie"] == 1
    assert s["field_status"]["remedy"]["G"] == {"correct": 1, "partial": 1}
    assert s["field_status"]["remedy"]["GL-pdf-high"] == {"partial": 1, "unsupported": 1}
    assert (
        s["unsupported_claims"]["GL-pdf-high"] == 1 and s["unsupported_claims"]["H-pdf-live"] == 1
    )
    assert s["unsupported_claims"]["G"] == 0
    assert s["usd"] == pytest.approx(1.75)


def test_the_estimate_command_prints_the_plan_and_the_cap_and_calls_nothing(pdf6_env, capsys):
    code = bs.main(
        [
            "--run-dir",
            str(pdf6_env.run_dir),
            "--judge-pdf",
            "--estimate",
            "--n-hard",
            "3",
            "--n-random",
            "3",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0 and "judge-pdf6: 6 call(s)" in out and "reading the PDF" in out
    assert "estimate $" in out and f"${bs.eb.CAP_USD:.2f} cap" in out and "fits under" in out
    assert pdf6_env.calls == [] and not (pdf6_env.run_dir / "judge-pdf6").exists()


def test_the_judge_command_writes_score_json_into_the_chosen_folder(pdf6_env, capsys):
    code = bs.main(
        [
            "--run-dir",
            str(pdf6_env.run_dir),
            "--judge-pdf",
            "--judge-dir",
            "judge-elsewhere",
            "--n-hard",
            "3",
            "--n-random",
            "2",
        ]
    )

    folder = pdf6_env.run_dir / "judge-elsewhere"
    assert code == 0
    assert {p.name for p in folder.iterdir()} == {"judge.jsonl", "judge_labels.json", "score.json"}
    assert json.loads((folder / "score.json").read_text())["judged"] == 5
    assert not (pdf6_env.run_dir / "judge-pdf6").exists()
    assert bs.main(["--run-dir", str(pdf6_env.run_dir), "--judge-dir", "x"]) == 2


async def test_the_summary_is_plain_text_tables_with_both_gemini_prices_and_no_html(pdf6_env):
    run = pdf6_env.run_dir
    await bs.run_judge_pdf6(run, n_hard=4, n_random=2)
    for arm in SIX:  # give every arm the live/batch bookkeeping a real run has
        rows = _lines(run / arm / "results.jsonl")
        _write_arm(run, arm, rows)

    path = report.write_pdf6_summary(run)

    text = path.read_text()
    assert path == run / "judge-pdf6" / "summary.md"
    assert "ARMS: validity and cost" in text and "EMPTY RATE per field" in text
    assert "APPLICABILITY recall" in text and "Best per field" in text
    assert "Status per field and arm" in text and "EXAMPLES where the arms differ most" in text
    assert "2026 (to 2026-12-31)" in text and "\n2027" in text
    assert "|" not in text.split("EXAMPLES")[0], "plain aligned columns, not markdown pipes"
    assert "Source per document: pdf 6" in text
    assert "(the judge's letters: A=" in text.split("EXAMPLES")[1], "judge text maps back to arms"
    assert not (run / "report.html").exists() and not (run / "report.md").exists()
    assert report.main(["--run-dir", str(run), "--pdf6"]) == 0


# --------------------------------------------------------------------------- effort arms


def test_a_reference_arm_replaces_the_stored_output_as_what_agreement_is_measured_against(
    tmp_path,
):
    ref = _out(symptoms=["engine stalls"], remedy="Replace the pump")
    same = _out(symptoms=["engine stalls"], remedy="Replace the pump")
    other = _out(symptoms=["no crank"], remedy="Reflash the module")
    _write_arm(tmp_path, "S-pdf", [_line("S-pdf", 1, ref), _line("S-pdf", 2, ref)])
    _write_arm(
        tmp_path, "H-pdf-high", [_line("H-pdf-high", 1, same), _line("H-pdf-high", 2, other)]
    )
    _write_arm(tmp_path, "H-pdf-max", [_line("H-pdf-max", 1, same), _line("H-pdf-max", 3, other)])
    _write_reference(tmp_path, [1, 2, 3])

    score = bs.score_run(tmp_path, ["S-pdf", "H-pdf-high", "H-pdf-max"], reference_arm="S-pdf")

    arms = score["arms"]
    assert score["reference_arm"] == "S-pdf" and "vs_reference" not in arms["S-pdf"]
    high = arms["H-pdf-high"]["vs_reference"]
    assert high["n"] == 2 and high["symptoms"] == pytest.approx(0.5)
    assert high["remedy"] == pytest.approx((1 + bs.token_f1(ref["remedy"], other["remedy"])) / 2)
    # doc 3 has no reference output, so only doc 1 counts for H-pdf-max
    assert arms["H-pdf-max"]["vs_reference"]["n"] == 1
    assert arms["H-pdf-max"]["vs_reference"]["symptoms"] == 1.0
    # without the option nothing changes
    assert "vs_reference" not in bs.score_run(tmp_path, ["S-pdf", "H-pdf-high"])["arms"]["S-pdf"]


def test_mean_latency_is_reported_when_the_result_lines_record_it_and_none_otherwise(tmp_path):
    timed = [{**_line("H-pdf-xhigh", i, _out()), "latency_s": s} for i, s in ((1, 10.0), (2, 20.0))]
    _write_arm(tmp_path, "H-pdf-xhigh", timed)
    _write_arm(tmp_path, "H-pdf-high", [_line("H-pdf-high", 1, _out())])
    _write_reference(tmp_path, [1, 2])

    arms = bs.score_run(tmp_path, ["H-pdf-xhigh", "H-pdf-high"])["arms"]

    assert arms["H-pdf-xhigh"]["latency_mean_s"] == pytest.approx(15.0)
    assert arms["H-pdf-high"]["latency_mean_s"] is None


def test_hard_documents_can_be_picked_by_every_compared_field_not_just_symptoms_and_remedy():
    base = _out()
    by_doc = {
        1: {"a": base, "b": _out(summary="Completely different words here"), "c": base},
        2: {"a": base, "b": base, "c": base},
        3: {"a": base, "b": _out(symptoms=["no crank"]), "c": base},
    }

    default_hard, _ = bs.select_judge_docs(by_doc, n_hard=1, n_random=0, min_valid=3)
    all_hard, _ = bs.select_judge_docs(
        by_doc, n_hard=1, n_random=0, min_valid=3, gap_fields=bs.ALL_GAP_FIELDS
    )

    assert default_hard == [3], "summary is invisible to the default gap"
    assert all_hard == [1], "summary now counts, and ties break on the lower id"
    assert bs.disagreement(by_doc[1]) == 0.0
    assert bs.disagreement(by_doc[1], bs.ALL_GAP_FIELDS) > 0.0


def test_score_cli_writes_to_score_out_and_restricts_to_the_subset(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    run.mkdir()
    _write_arm(run, "S-pdf", [_line("S-pdf", i, _out()) for i in (1, 2, 3)])
    _write_arm(run, "H-pdf-max", [_line("H-pdf-max", i, _out()) for i in (1, 2, 3)])
    _write_reference(run, [1, 2, 3])
    (run / "score.json").write_text("untouched")
    subset = tmp_path / "subset.json"
    subset.write_text(json.dumps([{"doc_id": 1}, {"doc_id": 2}]))
    monkeypatch.setattr(bs.eb, "SONNET_SAMPLE_PATH", subset)
    out = tmp_path / "effort-score.json"

    code = bs.main(
        ["--run-dir", str(run), "--arms", "S-pdf,H-pdf-max", "--subset"]
        + ["--reference-arm", "S-pdf", "--score-out", str(out)]
    )

    assert code == 0
    assert (run / "score.json").read_text() == "untouched"
    score = json.loads(out.read_text())
    assert score["arms"]["H-pdf-max"]["n"] == 2
    assert score["arms"]["H-pdf-max"]["vs_reference"]["symptoms"] == 1.0
    assert bs.main(["--run-dir", str(run), "--arms", "S-pdf", "--reference-arm", "nope"]) == 2
