"""Schema validation and cost accounting for the LLM stage."""

import json

import pytest

from src.jobs.llm import BATCH_DISCOUNT, cost_of, validate

VALID = {
    "summary": "Radio may exhibit a black screen after an over-the-air update.",
    "symptoms": ["black screen", "no audio"],
    "systems": ["infotainment"],
    "components": ["radio"],
    "remedy": "Reprogram the radio using SPS.",
    "applicability": "2024 Chevrolet Silverado EV",
    "doc_kind": "service_procedure",
    "applicability_years": [2024],
    "applicability_models": ["Silverado EV"],
    "applicability_conditions": "",
    "audience": "dealer",
}


def test_valid_payload_passes():
    clean, err = validate(VALID)
    assert err is None
    assert clean["summary"].startswith("Radio")
    assert clean["symptoms"] == ["black screen", "no audio"]


def test_json_string_is_parsed():
    clean, err = validate(json.dumps(VALID))
    assert err is None
    assert clean["doc_kind"] == "service_procedure"


def test_malformed_json_is_rejected():
    clean, err = validate('{"summary": "truncated mid-obj')
    assert clean is None
    assert "invalid JSON" in err


def test_missing_field_is_rejected():
    payload = {k: v for k, v in VALID.items() if k != "remedy"}
    clean, err = validate(payload)
    assert clean is None
    assert "remedy" in err


def test_empty_summary_is_rejected():
    """An empty summary is a silent failure that would otherwise be stored."""
    clean, err = validate({**VALID, "summary": "   "})
    assert clean is None
    assert "empty" in err


def test_wrong_type_for_array_is_rejected():
    clean, err = validate({**VALID, "symptoms": "black screen"})
    assert clean is None
    assert "must be a list" in err


def test_non_string_array_member_is_rejected():
    clean, err = validate({**VALID, "systems": ["infotainment", 42]})
    assert clean is None
    assert "only strings" in err


def test_unknown_doc_kind_is_rejected():
    clean, err = validate({**VALID, "doc_kind": "shopping_list"})
    assert clean is None
    assert "doc_kind" in err


def test_empty_arrays_are_allowed():
    """A warranty procedure legitimately has no symptoms; that is not an error."""
    clean, err = validate({**VALID, "symptoms": [], "doc_kind": "warranty_admin"})
    assert err is None
    assert clean["symptoms"] == []


def test_blank_array_members_are_dropped():
    clean, err = validate({**VALID, "symptoms": ["black screen", "  ", ""]})
    assert err is None
    assert clean["symptoms"] == ["black screen"]


def test_whitespace_is_stripped():
    clean, err = validate({**VALID, "remedy": "  Reprogram.  "})
    assert err is None
    assert clean["remedy"] == "Reprogram."


def test_non_object_payload_is_rejected():
    clean, err = validate("[1, 2, 3]")
    assert clean is None
    assert "expected object" in err


def test_missing_audience_is_rejected():
    payload = {k: v for k, v in VALID.items() if k != "audience"}
    clean, err = validate(payload)
    assert clean is None
    assert err == "missing fields: audience"


def test_unknown_audience_is_rejected():
    clean, err = validate({**VALID, "audience": "fleet"})
    assert clean is None
    assert "audience" in err


def test_years_are_coerced_and_junk_is_dropped():
    clean, err = validate({**VALID, "applicability_years": ["2018", 2019, "n/a"]})
    assert err is None
    assert clean["applicability_years"] == [2018, 2019]


def test_years_and_duplicate_models_are_cleaned():
    clean, err = validate(
        {
            **VALID,
            "applicability_years": ["2017", "2018"],
            "applicability_models": ["F-150", " F-150 ", ""],
            "audience": "dealer",
        }
    )
    assert err is None
    assert clean["applicability_years"] == [2017, 2018]
    assert clean["applicability_models"] == ["F-150"]
    assert clean["audience"] == "dealer"
    assert clean["applicability_conditions"] == ""


def test_the_prompt_carries_the_four_v2_rules():
    from src.jobs.llm import PDF_PROMPT

    for line in (
        "doc_kind: service_procedure only when the document prescribes steps a technician performs;",
        "audience: dealer when the document is addressed to dealers or technicians only;",
        "applicability_years are integers; applicability_models are the nameplates as printed,",
        "Never state that this document supersedes or is superseded by another unless the document says so explicitly",
    ):
        assert line in PDF_PROMPT


def test_batch_pricing_is_half_of_sync():
    sync = cost_of(1000, 500, batch=False)
    batch = cost_of(1000, 500, batch=True)
    assert batch == pytest.approx(sync * BATCH_DISCOUNT)


def test_cost_matches_published_rates():
    # Claude Haiku 5.5: 1M input tokens at $0.10, 1M output at $0.50.
    assert cost_of(1_000_000, 0, batch=False) == pytest.approx(0.10)
    assert cost_of(0, 1_000_000, batch=False) == pytest.approx(0.50)


def test_zero_tokens_costs_nothing():
    assert cost_of(0, 0, batch=True) == 0.0
