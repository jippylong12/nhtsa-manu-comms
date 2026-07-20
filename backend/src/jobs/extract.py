"""Job 2, first half: download PDFs and extract text locally.

Extracting locally with PyMuPDF is what keeps the pipeline cheap. Sending PDFs
straight to Gemini bills at image-token rates; the A1 spike measured that path
at 2.0x the input tokens and, on a table-dense document, slightly *less*
faithful (it invented a model-year range for a cell that was empty). So text
first, vision only for documents that fail the yield heuristic.
"""

import asyncio
import logging
import pathlib
import re
from dataclasses import dataclass, field
from typing import Optional

import fitz  # PyMuPDF
import httpx

from src.config import get_settings

log = logging.getLogger("extract")

# NHTSA document filenames are stable and unique (MC-11005396-0001.pdf), so the
# archive is keyed on them directly.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class ExtractionResult:
    """Outcome of extracting one document."""

    text: Optional[str] = None
    method: Optional[str] = None  # 'pymupdf' | 'vision-fallback'
    page_count: int = 0
    char_count: int = 0
    chars_per_page: float = 0.0
    alpha_ratio: float = 0.0
    empty_pages: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ExtractStats:
    downloaded: int = 0
    from_cache: int = 0
    extracted: int = 0
    flagged_vision: int = 0
    failed: int = 0
    skipped_done: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"downloaded={self.downloaded} cached={self.from_cache} extracted={self.extracted} "
            f"flagged_vision={self.flagged_vision} failed={self.failed} "
            f"skipped_already_done={self.skipped_done} errors={len(self.errors)}"
        )


def archive_path(url: str) -> pathlib.Path:
    """Local cache location for a document URL."""
    settings = get_settings()
    base = pathlib.Path(settings.pdf_archive_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    name = _SAFE_NAME.sub("_", url.rsplit("/", 1)[-1]) or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return base / name


async def download(
    client: httpx.AsyncClient, url: str
) -> tuple[pathlib.Path | None, str | None, bool]:
    """Fetch a PDF into the archive.

    Returns ``(path, error, from_cache)``. A previously archived file is never
    re-fetched, which is what makes re-runs cheap and keeps us polite to
    static.nhtsa.gov.
    """
    dest = archive_path(url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest, None, True

    last_error = None
    for attempt in range(3):
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                # A missing document is a permanent condition, not a transient
                # one; retrying wastes time and is impolite.
                return None, "http 404 (document not published)", False
            resp.raise_for_status()
            if not resp.content:
                return None, "empty response body", False
            dest.write_bytes(resp.content)
            return dest, None, False
        except httpx.HTTPStatusError as e:
            last_error = f"http {e.response.status_code}"
        except httpx.RequestError as e:
            last_error = f"{type(e).__name__}: {e}"
        if attempt < 2:
            await asyncio.sleep(2**attempt)
    return None, last_error or "download failed", False


def extract_text(path: pathlib.Path) -> ExtractionResult:
    """Extract text with PyMuPDF and apply the low-yield heuristic.

    A document below the threshold is flagged for the vision fallback rather
    than having unusable text stored: garbage text would silently poison both
    the LLM summary and the search index.
    """
    settings = get_settings()
    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001 - corrupt or non-PDF payload
        return ExtractionResult(error=f"pdf open failed: {type(e).__name__}: {e}")

    try:
        pages = [page.get_text("text") for page in doc]
    except Exception as e:  # noqa: BLE001
        return ExtractionResult(error=f"pdf parse failed: {type(e).__name__}: {e}")
    finally:
        doc.close()

    if not pages:
        return ExtractionResult(error="pdf has zero pages")

    full = "\n".join(pages)
    n_pages = len(pages)
    total = len(full)
    alpha = sum(c.isalpha() for c in full)
    result = ExtractionResult(
        page_count=n_pages,
        char_count=total,
        chars_per_page=round(total / n_pages, 2),
        # Recorded as a diagnostic only. The A1 spike found alpha_ratio tracks
        # table density, not garbling: the worst-scoring real document (0.471)
        # had pristine text and was simply full of model years and dashes.
        alpha_ratio=round(alpha / total, 3) if total else 0.0,
        empty_pages=sum(1 for p in pages if len(p) < 50),
    )

    if result.chars_per_page < settings.low_yield_chars_per_page:
        result.method = "vision-fallback"
        result.text = None
    else:
        result.method = "pymupdf"
        result.text = full
    return result


async def extract_pending(limit: int | None = None, force: bool = False) -> ExtractStats:
    """Download and extract every document that does not yet have text."""
    from src.db import get_pool

    settings = get_settings()
    stats = ExtractStats()
    pool = await get_pool()

    # A vision-fallback document has no extracted_text by design, so it must
    # not be re-selected here as though extraction still owed it work.
    unextracted = (
        "d.extracted_text IS NULL AND d.extraction_method IS DISTINCT FROM 'vision-fallback'"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT d.id, d.url, d.communication_id
            FROM comm_documents d
            JOIN communications c ON c.id = d.communication_id
            WHERE ({'TRUE' if force else unextracted})
              AND c.status = 'pending'
            ORDER BY d.id
            {f'LIMIT {int(limit)}' if limit else ''}
            """
        )

    if not rows:
        log.info("no documents need extraction")
        return stats

    log.info("extracting %d document(s)", len(rows))
    semaphore = asyncio.Semaphore(settings.pdf_download_concurrency)
    limits = httpx.Limits(max_connections=settings.pdf_download_concurrency)

    async with httpx.AsyncClient(
        timeout=45.0,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)"},
    ) as client:

        async def handle(row) -> None:
            async with semaphore:
                path, err, cached = await download(client, row["url"])
                if cached:
                    stats.from_cache += 1
                elif path:
                    stats.downloaded += 1
                    await asyncio.sleep(0.3)  # politeness spacing

            if err or path is None:
                stats.failed += 1
                stats.errors.append(f"{row['url']}: {err}")
                await _record_failure(pool, row, err or "download failed")
                return

            # PyMuPDF is CPU-bound and releases no GIL benefit from await, so
            # run it off the event loop to keep downloads flowing.
            result = await asyncio.to_thread(extract_text, path)
            if not result.ok:
                stats.failed += 1
                stats.errors.append(f"{row['url']}: {result.error}")
                await _record_failure(pool, row, result.error or "extraction failed")
                return

            if result.method == "vision-fallback":
                stats.flagged_vision += 1
                log.warning(
                    "%s: low yield (%.1f chars/page) -> flagged for vision fallback",
                    row["url"].rsplit("/", 1)[-1],
                    result.chars_per_page,
                )
            else:
                stats.extracted += 1

            await _store(pool, row["id"], result)

        await asyncio.gather(*(handle(r) for r in rows))

    log.info("extraction complete :: %s", stats.summary())
    return stats


async def _store(pool, doc_id: int, result: ExtractionResult) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE comm_documents
            SET extracted_text = $2, extraction_method = $3, page_count = $4,
                char_count = $5, chars_per_page = $6, alpha_ratio = $7,
                extracted_at = now()
            WHERE id = $1
            """,
            doc_id,
            result.text,
            result.method,
            result.page_count,
            result.char_count,
            result.chars_per_page,
            result.alpha_ratio,
        )


async def _record_failure(pool, row, reason: str) -> None:
    """Mark the owning communication failed with an explicit reason.

    Nothing is ever skipped silently: a document we could not read leaves a
    `failed` row explaining why, so the closeout report can account for it.
    """
    async with pool.acquire() as conn:
        # Guard on status <> 'processed' so a late failure from a sibling
        # document can never drag an already-processed communication back to
        # failed and null its processed_at.
        await conn.execute(
            """
            UPDATE communications
            SET status = 'failed',
                status_reason = $2,
                attempts = attempts + 1
            WHERE id = $1 AND status <> 'processed'
            """,
            row["communication_id"],
            f"document {row['url'].rsplit('/', 1)[-1]}: {reason}"[:500],
        )


async def mark_documentless_communications() -> int:
    """Flag pending communications that have no attached PDF at all.

    NHTSA lists some communications without publishing a document. They can
    never be processed, so they are recorded as failed with a reason rather
    than sitting in `pending` forever and skewing the drain metrics.
    """
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE communications c
            SET status = 'failed', status_reason = 'no associated documents published by NHTSA'
            WHERE c.status = 'pending'
              AND NOT EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id = c.id)
            """
        )
    n = int(result.split()[-1]) if result else 0
    if n:
        log.info("marked %d communication(s) with no documents as failed", n)
    return n
