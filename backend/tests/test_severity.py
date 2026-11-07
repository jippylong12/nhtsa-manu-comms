"""Severity by NHTSA type, and the rule-based classifier for catch-all types."""

import pytest

from src.communications.schemas import COMM_TYPE_MAP
from src.communications.severity import (
    CATCH_ALL_TYPES,
    SEVERITY_BY_TYPE,
    classify_from_document,
    severity_for_type,
)

TIERS = {"serious", "medium", "small"}


@pytest.mark.parametrize("comm_type", sorted(COMM_TYPE_MAP))
def test_every_known_type_maps_to_a_tier(comm_type):
    assert severity_for_type(comm_type) in TIERS
    assert comm_type in SEVERITY_BY_TYPE


def test_tier_examples():
    assert severity_for_type("TSB") == "serious"
    assert severity_for_type("WA") == "medium"
    assert severity_for_type("MC") == "small"
    assert severity_for_type(None) == "small"
    assert severity_for_type("NOPE") == "small"


def test_catch_all_types():
    assert CATCH_ALL_TYPES == {"MC", "NA", "OTHER", "BL"}


@pytest.mark.parametrize(
    "args, expected",
    [
        (("recall", None, None, None), "serious"),
        (("customer_letter", None, "Dealer will replace the part", "owner"), "serious"),
        (("customer_letter", None, "", "owner"), "small"),
        (("service_procedure", None, None, None), "medium"),
        (("informational", ["noise"], "Replace the mount", None), "medium"),
        (("informational", None, None, None), "small"),
        (("warranty_admin", None, None, None), "small"),
        (("recall", None, None, "dealer"), "medium"),
        ((None, None, None, None), "small"),
    ],
)
def test_classifier_examples(args, expected):
    severity, reason = classify_from_document(*args)
    assert severity == expected
    assert reason
