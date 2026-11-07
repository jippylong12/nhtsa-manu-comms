"""Input planner: which form of a document is presented to the model.

Pure of database access. A document goes as the PDF itself when it is small
enough, as extracted text when it is long but its text layer is usable, and as
110 DPI page-image PDF chunks when it is long and its text is garbled.
"""

import pathlib
from dataclasses import dataclass
from typing import Literal

import fitz  # PyMuPDF
import httpx

from src.jobs import extract

MAX_DIRECT_PAGES = 30
MAX_PAYLOAD_BYTES = 25 * 1024 * 1024
SHRINK_DPI = 110
CHUNK_PAGES = 30
_FALLBACK_DPI = 80


@dataclass(frozen=True)
class InputPlan:
    """How one document is sent: one PDF, extracted text, or PDF chunks."""

    kind: Literal["pdf", "text", "chunks"]
    payloads: tuple[bytes, ...]
    reason: str


def _render(path: pathlib.Path, first: int, last: int, dpi: int) -> bytes:
    """Pages ``first..last`` (exclusive end) as a PDF of full-page JPEG images."""
    out = fitz.open()
    with fitz.open(str(path)) as src:
        for index in range(first, min(last, src.page_count)):
            page = src[index]
            pix = page.get_pixmap(dpi=dpi)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=pix.tobytes("jpeg"))
    data = out.tobytes(deflate=True)
    out.close()
    return data


def shrink(path: pathlib.Path, dpi: int | None = None) -> bytes:
    """Re-render every page as a JPEG image; retry at 80 DPI if still too big."""
    dpi = SHRINK_DPI if dpi is None else dpi
    with fitz.open(str(path)) as src:
        count = src.page_count
    data = _render(path, 0, count, dpi)
    if len(data) > MAX_PAYLOAD_BYTES and dpi > _FALLBACK_DPI:
        data = _render(path, 0, count, _FALLBACK_DPI)
    return data


def render_chunks(
    path: pathlib.Path, pages_per_chunk: int = CHUNK_PAGES, dpi: int | None = None
) -> list[bytes]:
    """Consecutive page ranges of at most ``pages_per_chunk``, as image PDFs."""
    dpi = SHRINK_DPI if dpi is None else dpi
    with fitz.open(str(path)) as src:
        count = src.page_count
    return [
        _render(path, start, start + pages_per_chunk, dpi)
        for start in range(0, count, pages_per_chunk)
    ]


def plan(
    path: pathlib.Path, page_count: int, extraction_method: str | None, has_text: bool
) -> InputPlan:
    """Decide the input form for a document and produce its payload bytes."""
    if page_count <= MAX_DIRECT_PAGES:
        if path.stat().st_size <= MAX_PAYLOAD_BYTES:
            return InputPlan("pdf", (path.read_bytes(),), "direct")
        return InputPlan("pdf", (shrink(path),), "shrunk")
    if extraction_method == "pymupdf" and has_text:
        return InputPlan("text", (), "long document with usable text")
    return InputPlan("chunks", tuple(render_chunks(path)), "long document without usable text")


async def ensure_archived(url: str) -> pathlib.Path:
    """The archived PDF for ``url``, downloading it only when it was pruned."""
    path = extract.archive_path(url)
    if path.exists() and path.stat().st_size > 0:
        return path
    async with httpx.AsyncClient(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)"},
    ) as client:
        downloaded, error, _ = await extract.download(client, url)
    if downloaded is None:
        raise RuntimeError(f"pdf unavailable: {error}")
    return downloaded
