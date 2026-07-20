"""Low-yield heuristic and extraction edge cases.

PDFs are synthesised with PyMuPDF so the thresholds are exercised
deterministically, without depending on the network or on a particular NHTSA
document staying published.
"""

import fitz
import pytest

from src.config import get_settings
from src.jobs.extract import archive_path, extract_text


def _make_pdf(path, pages_text: list[str]) -> None:
    doc = fitz.open()
    for body in pages_text:
        page = doc.new_page()
        if body:
            page.insert_textbox(fitz.Rect(40, 40, 560, 780), body, fontsize=9)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def dense_pdf(tmp_path):
    """A normal bulletin: comfortably above the threshold."""
    body = (
        "This service bulletin describes a condition where the vehicle exhibits "
        "a shudder during light acceleration between 40 and 60 mph. "
    ) * 12
    path = tmp_path / "dense.pdf"
    _make_pdf(path, [body, body])
    return path


@pytest.fixture
def sparse_pdf(tmp_path):
    """A scan-like document: almost no text layer."""
    path = tmp_path / "sparse.pdf"
    _make_pdf(path, ["x", "y"])
    return path


def test_dense_document_uses_local_extraction(dense_pdf):
    result = extract_text(dense_pdf)

    assert result.ok
    assert result.method == "pymupdf"
    assert result.text is not None
    assert "shudder" in result.text
    assert result.page_count == 2
    assert result.chars_per_page > get_settings().low_yield_chars_per_page


def test_low_yield_document_is_flagged_not_stored(sparse_pdf):
    """Garbled text must not be persisted: it would poison both the LLM
    summary and the search index."""
    result = extract_text(sparse_pdf)

    assert result.ok
    assert result.method == "vision-fallback"
    assert result.text is None, "unusable text must not be stored"
    assert result.chars_per_page < get_settings().low_yield_chars_per_page


def test_threshold_boundary_is_respected(tmp_path, monkeypatch):
    """A document just over the line is kept; just under is flagged."""
    from src.config import Settings
    from src.config import get_settings as _gs

    _gs.cache_clear()
    monkeypatch.setenv("LOW_YIELD_CHARS_PER_PAGE", "500")
    _gs.cache_clear()
    assert Settings().low_yield_chars_per_page == 500

    over = tmp_path / "over.pdf"
    _make_pdf(over, ["A" * 900])
    assert extract_text(over).method == "pymupdf"

    under = tmp_path / "under.pdf"
    _make_pdf(under, ["A" * 100])
    assert extract_text(under).method == "vision-fallback"

    monkeypatch.delenv("LOW_YIELD_CHARS_PER_PAGE", raising=False)
    _gs.cache_clear()


def test_corrupt_file_reports_error_not_crash(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"this is definitely not a pdf")

    result = extract_text(bad)

    assert not result.ok
    assert result.error is not None
    assert result.text is None


def test_metrics_are_recorded(dense_pdf):
    result = extract_text(dense_pdf)

    assert result.page_count == 2
    assert result.char_count > 0
    assert result.chars_per_page == round(result.char_count / 2, 2)
    assert 0.0 < result.alpha_ratio <= 1.0


def test_empty_pages_are_counted(tmp_path):
    body = "Real content here. " * 40
    path = tmp_path / "mixed.pdf"
    _make_pdf(path, [body, "", body])

    result = extract_text(path)

    assert result.page_count == 3
    assert result.empty_pages == 1, "blank separator pages should be counted, not fatal"
    assert result.method == "pymupdf", "one blank page must not condemn the whole document"


def test_archive_path_is_stable_and_sanitised():
    a = archive_path("https://static.nhtsa.gov/odi/tsbs/2024/MC-11005396-0001.pdf")
    b = archive_path("https://static.nhtsa.gov/odi/tsbs/2024/MC-11005396-0001.pdf")

    assert a == b, "the same URL must map to the same cache file, or PDFs re-download"
    assert a.name == "MC-11005396-0001.pdf"


def test_archive_path_rejects_traversal():
    path = archive_path("https://evil.example/../../etc/passwd")

    assert ".." not in path.name
    assert path.name.endswith(".pdf")
