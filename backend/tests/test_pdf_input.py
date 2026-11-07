"""Input planner: which form of a document goes to the model.

PDFs are synthesised with PyMuPDF; no network and no model calls.
"""

import fitz
import pytest

from src.jobs import extract, pdf_input


def _make_pdf(path, pages: int) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(40, 40, 560, 780), f"Service bulletin page {i + 1}. " * 40, fontsize=9
        )
    doc.save(str(path))
    doc.close()


def _page_count(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def test_short_pdf_goes_to_the_model_as_the_original_file(tmp_path):
    path = tmp_path / "short.pdf"
    _make_pdf(path, 3)

    result = pdf_input.plan(path, 3, "pymupdf", True)

    assert result.kind == "pdf"
    assert result.payloads == (path.read_bytes(),)


def test_long_pdf_with_usable_text_goes_as_text_with_no_payload(tmp_path):
    path = tmp_path / "long.pdf"
    _make_pdf(path, 31)

    result = pdf_input.plan(path, 31, "pymupdf", True)

    assert result.kind == "text"
    assert result.payloads == ()


def test_long_vision_fallback_pdf_is_split_into_valid_chunks(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_pdf(path, 31)

    result = pdf_input.plan(path, 31, "vision-fallback", False)

    assert result.kind == "chunks"
    assert [_page_count(p) for p in result.payloads] == [30, 1]


def test_oversize_short_pdf_is_shrunk_and_stays_pdf(tmp_path, monkeypatch):
    path = tmp_path / "heavy.pdf"
    _make_pdf(path, 2)
    monkeypatch.setattr(pdf_input, "MAX_PAYLOAD_BYTES", 1024)

    result = pdf_input.plan(path, 2, "pymupdf", True)

    assert result.kind == "pdf"
    assert result.reason == "shrunk"
    assert len(result.payloads) == 1
    assert _page_count(result.payloads[0]) == 2


def test_oversize_pdf_is_actually_smaller_after_shrinking(tmp_path, monkeypatch):
    import os

    path = tmp_path / "heavy.pdf"
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 3000, 3000), False)
        pix.set_rect(pix.irect, (200, 30, 90))
        page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(str(path))
    doc.close()
    # Noise-free large images compress well; pad the file past the (lowered) cap.
    monkeypatch.setattr(pdf_input, "MAX_PAYLOAD_BYTES", path.stat().st_size - 1)
    monkeypatch.setattr(pdf_input, "SHRINK_DPI", 30)
    assert os.path.getsize(path) > pdf_input.MAX_PAYLOAD_BYTES

    result = pdf_input.plan(path, 2, "pymupdf", True)

    assert result.reason == "shrunk"
    assert len(result.payloads[0]) < path.stat().st_size


async def test_archived_document_is_not_downloaded_again(tmp_path, monkeypatch):
    archived = tmp_path / "a.pdf"
    archived.write_bytes(b"%PDF-1.4 archived")
    monkeypatch.setattr(extract, "archive_path", lambda url: archived)

    async def boom(client, url):
        raise AssertionError("download must not be called")

    monkeypatch.setattr(extract, "download", boom)

    assert await pdf_input.ensure_archived("https://x/a.pdf") == archived


async def test_pruned_document_is_downloaded_once(tmp_path, monkeypatch):
    target = tmp_path / "b.pdf"
    monkeypatch.setattr(extract, "archive_path", lambda url: target)
    calls = []

    async def fake_download(client, url):
        calls.append(url)
        target.write_bytes(b"%PDF-1.4 fresh")
        return target, None, False

    monkeypatch.setattr(extract, "download", fake_download)

    assert await pdf_input.ensure_archived("https://x/b.pdf") == target
    assert calls == ["https://x/b.pdf"]


async def test_failed_download_raises_pdf_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "archive_path", lambda url: tmp_path / "missing.pdf")

    async def fake_download(client, url):
        return None, "http 404 (document not published)", False

    monkeypatch.setattr(extract, "download", fake_download)

    with pytest.raises(RuntimeError, match="pdf unavailable: http 404"):
        await pdf_input.ensure_archived("https://x/missing.pdf")
