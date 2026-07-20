"""Composed embedding input and post-truncation normalisation."""

import math

from src.jobs.embed import compose_input, normalize, to_pgvector


def test_compose_leads_with_summary():
    text = compose_input(
        {
            "llm_summary": "Brake shudder during light braking.",
            "symptoms": ["shudder", "pulsation"],
            "systems": ["brakes"],
            "components": ["rotor"],
        }
    )
    assert text.startswith("Brake shudder")
    assert "Symptoms: shudder, pulsation" in text
    assert "Systems: brakes" in text
    assert "Components: rotor" in text


def test_compose_tolerates_missing_tags():
    text = compose_input(
        {"llm_summary": "A summary.", "symptoms": [], "systems": [], "components": []}
    )
    assert text == "A summary."


def test_compose_handles_all_empty():
    assert (
        compose_input({"llm_summary": None, "symptoms": None, "systems": None, "components": None})
        == ""
    )


def test_normalize_produces_unit_vector():
    """Matryoshka truncation to 1536 breaks the model's native normalisation,
    so cosine distance needs this to be correct."""
    v = normalize([3.0, 4.0])  # magnitude 5
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)
    assert math.isclose(v[0], 0.6) and math.isclose(v[1], 0.8)


def test_normalize_handles_zero_vector():
    assert normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_pgvector_format():
    assert to_pgvector([0.1, 0.2, 0.3]).startswith("[")
    assert to_pgvector([0.1, 0.2, 0.3]).endswith("]")
    assert to_pgvector([1.0, 2.0]).count(",") == 1
