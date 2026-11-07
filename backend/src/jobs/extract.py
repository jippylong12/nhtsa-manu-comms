"""Job 2, first half: download PDFs and extract text locally.

Text is still extracted locally for full-text search, the page count and the
yield flag. The model reads the PDF itself (see docs/adr/0002).
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


async def prune_archive(dry_run: bool = False) -> tuple[int, int]:
    """Delete archived PDFs whose document is fully processed.

    The archive doubles as a download cache and nothing else expires it, so it
    grows without bound: one file per communication, a few hundred KB each.

    A PDF stops being needed once its document has an ``llm_summary``. Before
    that it is still live input, because the LLM stage reads the PDF itself
    from here (``pdf_input.ensure_archived``). Rows
    without a summary are therefore left alone, which also means an interrupted
    run loses nothing.

    Deleting is safe rather than merely tidy: the extracted text and the summary
    are already in Postgres, and a later re-extract re-downloads from NHTSA.

    Returns ``(files_removed, bytes_freed)``.
    """
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT url FROM comm_documents WHERE llm_summary IS NOT NULL")

    removed = 0
    freed = 0
    for row in rows:
        path = archive_path(row["url"])
        if not path.exists():
            continue
        freed += path.stat().st_size
        if not dry_run:
            path.unlink(missing_ok=True)
        removed += 1

    verb = "would free" if dry_run else "freed"
    log.info("archive prune: %d file(s), %s %.1f MB", removed, verb, freed / 1_048_576)
    return removed, freed


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


def _strip_unstorable(text: str) -> str:
    """Remove code points Postgres refuses to store in a text column.

    A malformed PDF can yield NUL bytes, and Postgres rejects those outright
    with "invalid byte sequence for encoding UTF8: 0x00", which used to abort
    the whole extraction run. The surrogate range is dropped for the same
    reason: it survives Python but not a UTF-8 encode on the way to the driver.

    Only these code points go; everything else is left alone so the low-yield
    heuristic still sees the document as PyMuPDF read it.
    """
    cleaned = text.replace("\x00", "")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError:
        # Lone surrogates: encode with errors="ignore" to drop exactly those.
        cleaned = cleaned.encode("utf-8", "ignore").decode("utf-8")
    return cleaned


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

    full = _strip_unstorable("\n".join(pages))
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


async def extract_pending(
    limit: int | None = None,
    force: bool = False,
    vehicle_id: int | None = None,
) -> ExtractStats:
    """Download and extract every document that does not yet have text.

    `vehicle_id` restricts the pass to one tracked vehicle, which is what
    an in-app refresh needs so its bill matches the estimate it quoted. None
    means every vehicle, which is what the CLI passes.
    """
    from src.db import get_pool
    from src.jobs.repository import vehicle_scope

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
              AND {vehicle_scope('$1')}
            ORDER BY d.id
            {f'LIMIT {int(limit)}' if limit else ''}
            """,
            vehicle_id,
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

        async def handle_isolated(row) -> None:
            """One unusable document must cost one document, not the run.

            gather() propagates the first exception, so before this an
            unexpected error partway through a batch of several hundred threw
            away every sibling still in flight and left them pending.
            """
            try:
                await handle(row)
            except Exception as e:  # noqa: BLE001 - one bad document, keep going
                stats.failed += 1
                stats.errors.append(f"{row['url']}: {type(e).__name__}: {e}")
                log.warning("%s: %s: %s", row["url"], type(e).__name__, e)
                await _record_failure(pool, row, f"{type(e).__name__}: {e}")

        await asyncio.gather(*(handle_isolated(r) for r in rows))

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


async def mark_documentless_communications(vehicle_id: int | None = None) -> int:
    """Flag pending communications that have no attached PDF at all.

    NHTSA lists some communications without publishing a document. They can
    never be processed, so they are recorded as failed with a reason rather
    than sitting in `pending` forever and skewing the drain metrics.
    """
    from src.db import get_pool
    from src.jobs.repository import vehicle_scope

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"""
            UPDATE communications c
            SET status = 'failed', status_reason = 'no associated documents published by NHTSA'
            WHERE c.status = 'pending'
              AND NOT EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id = c.id)
              AND {vehicle_scope('$1')}
            """,
            vehicle_id,
        )
    n = int(result.split()[-1]) if result else 0
    if n:
        log.info("marked %d communication(s) with no documents as failed", n)
    return n
