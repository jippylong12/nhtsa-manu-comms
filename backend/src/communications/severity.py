"""Severity of a communication: fixed by NHTSA type, refined by a classifier.

Severity by type is a fixed mapping, the same tiers the frontend uses
(``COMM_PRIORITY_TYPES`` in ``frontend/src/client/types.ts``). For the catch-all
types (MC, NA, OTHER, BL) the type says little, so ``classify_from_document``
reads the extracted document instead. Its rules are a proposed rule-based v1,
not agreed requirements; see the M3 report for the evidence and open questions.
"""

SEVERITY_BY_TYPE: dict[str, str] = {
    **dict.fromkeys(("TSB", "CSP", "RC"), "serious"),
    **dict.fromkeys(("PIT", "PIC", "SB", "TB", "SU", "WA", "SC"), "medium"),
    **dict.fromkeys(("PIP", "IB", "BL", "OL", "DL", "MC", "NA", "OTHER"), "small"),
}

CATCH_ALL_TYPES = frozenset({"MC", "NA", "OTHER", "BL"})


def severity_for_type(comm_type: str | None) -> str:
    """Return the tier for an NHTSA communication type; unknown or None is small."""
    return SEVERITY_BY_TYPE.get(comm_type or "", "small")


def classify_from_document(
    doc_kind: str | None,
    symptoms: list[str] | None,
    remedy: str | None,
    audience: str | None,
) -> tuple[str, str]:
    """Return ``(severity, reason)`` for a processed document (proposed v1 rules)."""
    has_remedy = bool(remedy and remedy.strip())
    if doc_kind == "recall":
        severity, reason = "serious", "recall"
    elif doc_kind == "customer_letter" and has_remedy:
        severity, reason = "serious", "owner letter with remedy"
    elif doc_kind == "service_procedure":
        severity, reason = "medium", "service procedure"
    elif doc_kind == "informational" and symptoms and has_remedy:
        severity, reason = "medium", "informational with symptoms and remedy"
    else:
        severity, reason = "small", "no escalating signal"
    if audience == "dealer" and severity == "serious":
        return "medium", "dealer-facing capped"
    return severity, reason
