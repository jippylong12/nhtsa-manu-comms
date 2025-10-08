"""Filtering, URL extraction, grouping, and printing helpers."""
from typing import Any, Dict, List, Optional

from config import TARGET_YEAR, TARGET_MODEL, KEYWORDS


def product_matches(comm: Dict[str, Any]) -> bool:
    """Return True if the communication references the configured product.

    Matches by exact year and case-insensitive model string.
    """
    products = comm.get("associatedProducts") or []
    for p in products:
        year = str(p.get("productYear", "")).strip()
        model = str(p.get("productModel", "")).strip()
        if year == TARGET_YEAR and model.upper() == TARGET_MODEL.upper():
            return True
    return False


essential_whitespace = str.split


def is_update_related(summary: Optional[str]) -> bool:
    """Return True if the summary contains any configured KEYWORDS as whole words."""
    if not summary:
        return False
    words = {w.strip().lower() for w in str(summary).split() if w.strip()}
    keywords = {str(k).strip().lower() for k in KEYWORDS if isinstance(k, str) and k.strip()}
    if not keywords:
        return False
    return any(k in words for k in keywords)


def extract_document_urls(comm: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract document URLs grouped by summary type."""
    urls_by_summary: Dict[str, List[str]] = {}
    docs = comm.get("associatedDocuments") or []
    for d in docs:
        url = d.get("url")
        summary = d.get("summary", "Unknown")
        if isinstance(url, str) and url:
            if summary not in urls_by_summary:
                urls_by_summary[summary] = []
            urls_by_summary[summary].append(url)
    return urls_by_summary


def filter_update_related_comms(comms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter communications to those matching the configured product and keywords."""
    return [c for c in comms if product_matches(c) and is_update_related(c.get("summary"))]


def group_urls_by_summary(comms: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Aggregate associated document URLs across communications, grouped by summary label."""
    url_results: Dict[str, List[str]] = {}
    for c in comms:
        urls_by_summary = extract_document_urls(c)
        for summary, urls in urls_by_summary.items():
            if summary not in url_results:
                url_results[summary] = []
            url_results[summary].extend(urls)
    return url_results


def print_grouped_urls(url_results: Dict[str, List[str]], header_text: str) -> None:
    """Pretty-print grouped URLs or a helpful message when none are present."""
    if not url_results:
        print(f"No associated document URLs found for {header_text}.")
        return

    print(f"Associated document URLs for {header_text}:\n")
    for summary, urls in url_results.items():
        print(f"=== {summary} ===")
        for u in urls:
            print(f"  {u}")
        print()