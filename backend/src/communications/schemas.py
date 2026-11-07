"""Communication type detection.

Only the classifier is left. The response, stats and fetch models that used to
live here described the Mongo-backed endpoints, and those are gone (JIP-417):
reading communications is `/api/corpus`, fetching them is `/api/refresh`.

`get_comm_type` is still the single place that decides what kind of bulletin
something is, called by sync as it writes each row.
"""

from typing import Optional

# --- Communication Types ---
# Extended categorization based on:
# 1. Manufacturer communication number prefix (PIT, PIC, PIP, TSB)
# 2. Summary text analysis for bulletins/programs
# 3. Communication number patterns (XX-NA-XXX format)

# Severity tier per type is defined in severity.py (SEVERITY_BY_TYPE); keep them in step.
COMM_TYPE_MAP = {
    # Prefix-based types
    "TSB": "Technical Service Bulletin",
    "PIT": "Preliminary Info Technical",
    "PIC": "Preliminary Info Customer",
    "PIP": "Preliminary Info Parts",
    # Summary-based types (detected from text)
    "SB": "Service Bulletin",
    "TB": "Technical Bulletin",
    "IB": "Informational Bulletin",
    "SU": "Service Update",
    "WA": "Warranty Administration",
    "CSP": "Customer Satisfaction Program",
    "RC": "Recall/Campaign",
    "SC": "Special Coverage",
    # Document-based types (from associated_documents.summary)
    "OL": "Owner Letter",
    "DL": "Dealer Letter",
    "BL": "Bulletin",  # Generic bulletin (catch-all from document type)
    "MC": "Manufacturer Communication",  # Generic comm catch-all
    # Format-based types (catchall before OTHER)
    "NA": "NA Bulletin",  # XX-NA-XXX format
    # Default
    "OTHER": "Other",
}


# Document summary to type code mapping
DOC_SUMMARY_TYPE_MAP = {
    "owner letter": "OL",
    "dealer letter": "DL",
    "service bulletin document": "SB",
    "bulletin": "BL",
    "manufacturer communications": "MC",
}


def get_comm_type_from_doc_summary(documents: list[dict] | None) -> str | None:
    """Extract communication type from associated document summary field.

    The API returns document types like 'Owner Letter', 'Bulletin', 'Dealer Letter', etc.
    in the associated_documents[].summary field.
    """
    if not documents:
        return None

    for doc in documents:
        summary = (doc.get("summary") or "").lower().strip()
        if summary in DOC_SUMMARY_TYPE_MAP:
            return DOC_SUMMARY_TYPE_MAP[summary]

    return None


def get_comm_type_from_prefix(comm_number: Optional[str]) -> Optional[str]:
    """Extract communication type from manufacturer communication number prefix."""
    if not comm_number:
        return None
    prefix = comm_number[:3].upper()
    if prefix in ("TSB", "PIT", "PIC", "PIP"):
        return prefix
    # NA format check moved to get_comm_type as fallback
    return None


def get_comm_type_from_summary(summary: Optional[str]) -> Optional[str]:
    """Extract communication type by analyzing the summary text."""
    if not summary:
        return None

    text = summary.lower()

    # Order matters - check more specific patterns first
    if "customer satisfaction" in text:
        return "CSP"
    if "special coverage" in text:
        return "SC"
    if "safety recall" in text or "recall campaign" in text:
        return "RC"
    if "warranty administration" in text:
        return "WA"
    # Check for bulletin type patterns with "provides" language
    if "technical service bulletin" in text or "this technical bulletin provides" in text:
        return "TSB"
    if "technical bulletin" in text:
        return "TB"
    if "this service bulletin provides" in text or "service bulletin" in text:
        return "SB"
    if "service update" in text:
        return "SU"
    if "informational bulletin" in text or "this bulletin provides information" in text:
        return "IB"
    if "preliminary information" in text:
        return "PIT"

    return None


def is_na_format(comm_number: Optional[str]) -> bool:
    """Check if communication number follows XX-NA-XXX format."""
    if not comm_number:
        return False
    upper = comm_number.upper()
    return "-NA-" in upper or upper.endswith("-NA")


def get_comm_type(
    comm_number: Optional[str],
    summary: Optional[str] = None,
    documents: list[dict] | None = None,
) -> str:
    """
    Determine communication type using multiple detection methods.
    Priority: 1) Prefix-based, 2) Summary text, 3) Document type, 4) NA format, 5) OTHER
    """
    # First try prefix-based detection (TSB, PIT, PIC, PIP)
    prefix_type = get_comm_type_from_prefix(comm_number)
    if prefix_type:
        return prefix_type

    # Then try summary text-based detection
    if summary:
        summary_type = get_comm_type_from_summary(summary)
        if summary_type:
            return summary_type

    # Try document type detection (Owner Letter, Bulletin, etc.)
    if documents:
        doc_type = get_comm_type_from_doc_summary(documents)
        if doc_type:
            return doc_type

    # NA format is the last catchall before OTHER
    if is_na_format(comm_number):
        return "NA"

    return "OTHER"
