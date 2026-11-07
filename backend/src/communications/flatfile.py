"""The flat-file communications index: download, parse, ingest (ADR 0001, M3).

NHTSA publishes the whole manufacturer-communications corpus as daily-refreshed
flat files on static.nhtsa.gov (S3/CloudFront -- no API gateway in the path),
keyed exactly on ``(NHTSA ID, Make, Model, Model Year)`` in plain-nameplate
vocabulary. This module is the ingest that turns those files into the
``comm_products`` table, which the add-vehicle picker reads and which sync uses
to learn a vehicle's communications -- replacing the byYmmt/``get_vehicle_details``
chain and its 30s-timeout failure mode entirely.

Layout of the source (see ``.../tsbs/TSBS.txt``): eight ``MFR_COMMS_RECEIVED_*.zip``
files, one per 5-year window of *date received by NHTSA*. Each zip holds one CSV,
one record per ``(TSB/Document ID, Make, Model, Model Year)`` -- so a bulletin
covering five products is five rows. The CSV's first column is labelled
"TSB/Document ID" but its *values* are the NHTSA ID Number that joins to
``communications.nhtsa_id`` (verified against production); the manufacturer's own
document id lives only inside the summary text.

Idempotency is ETag-driven. NHTSA re-stamps every window's ``Last-Modified`` daily
even when nothing changed, so the S3 ETag -- recorded per window in
``flatfile_ingests`` -- is the change signal. A window is re-ingested only when its
ETag differs from the last ingest; re-ingest is delete-by-window then insert,
inside one transaction, so a partial failure never leaves a half-replaced window.

Usage:
    python -m src.communications.flatfile --bootstrap   # ingest all 8 windows
    python -m src.communications.flatfile --refresh      # re-ingest changed windows
    python -m src.communications.flatfile --status       # show per-window ingest state
"""

import argparse
import asyncio
import csv
import io
import logging
import re
import sys
import tempfile
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Iterator, Optional

import asyncpg
import httpx

from src.db import close_pool, get_pool

log = logging.getLogger("flatfile")

BASE_URL = "https://static.nhtsa.gov/odi/ffdd/tsbs"
_BUCKET_LISTING = "https://static.nhtsa.gov/?list-type=2&prefix=odi/ffdd/tsbs/"
_FILENAME = "MFR_COMMS_RECEIVED_{window}.zip"

# The known windows as of M3 (confirmed against the S3 listing). discover_windows()
# reads the live bucket listing and falls back to this when the listing is
# unreachable, so a window NHTSA adds later (e.g. a future 2030-2034) is picked up
# without a code change, and one that disappears is skipped.
WINDOWS_FALLBACK: tuple[str, ...] = (
    "1995-1999",
    "2000-2004",
    "2005-2009",
    "2010-2014",
    "2015-2019",
    "2020-2024",
    "2025-2025",
    "2025-2026",
)

# The CSV header, exactly as published. Parsing keys off column position but this
# documents the shape and lets parse_rows tolerate a header row.
CSV_COLUMNS = ("TSB/Document ID", "Make", "Model", "Model Year", "Concise Summary")

_HEADERS = {
    "User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)",
    "Accept": "*/*",
}
# CSV field can be a 4000-char summary; the default 128 KB limit is plenty, but be
# explicit so an unusually long field never raises field-size errors mid-window.
csv.field_size_limit(10 * 1024 * 1024)


# --------------------------------------------------------------------------
# Normalization (ADR 0001 section 5, measured 98-100% on cars in JIP-428)
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize_model(raw: Optional[str]) -> str:
    """Canonical nameplate: uppercase, hyphen->space, collapse whitespace.

    This is the form stored in ``comm_products.model`` and the form every match
    is made against, so ``F-150`` and ``F 150`` collapse to one nameplate
    (``F 150``) and ``MUSTANG MACH-E`` meets ``MUSTANG MACH E``. Applied
    identically on both sides of every comparison -- ingest and lookup -- so the
    picker lists, and sync matches, exactly the same strings.
    """
    if not raw:
        return ""
    return _WS.sub(" ", raw.upper().replace("-", " ")).strip()


def compact_model(raw: Optional[str]) -> str:
    """Compact form: uppercase with all spaces and hyphens removed.

    The fallback matcher for the M4 backfill, where the canonical form still
    misses (``CMAX`` vs ``C-MAX``, ``4500 HD`` vs ``4500HD``). Lives here so the
    one normalization vocabulary has a single home and can be unit-tested.
    """
    if not raw:
        return ""
    return raw.upper().replace("-", "").replace(" ", "")


# NHTSA's own duplicate markers: a product row filed as ``REDUNDANT F 250`` or
# ``DUPLICATE RA1250`` is a second copy of a nameplate it also files elsewhere.
JUNK_PREFIX = re.compile(r"^(?:REDUNDANT|DUPLICATE)\s+")


# A junk name's key must be at least this long before a prefix/suffix match may
# resolve it: "Z" or "86" would otherwise attach to unrelated nameplates.
_MIN_ALIAS_KEY = 3


def resolve_models(counts: dict[str, int]) -> dict[str, str]:
    """Map every indexed name for one (model_year, make) to the name it is listed as.

    ``counts`` is each distinct ``comm_products.model`` with its distinct bulletin
    count. The picker lists the distinct values of the result, and matching takes
    every name that resolves to a vehicle's model, so the two cannot disagree: a
    bulletin filed under a name that is folded away stays reachable through the name
    it folds into.

    * Real names with the same compact key (``F53`` / ``F 53``) fold into the
      spelling with more bulletins; a tie keeps the one with more spaces.
    * A ``REDUNDANT``/``DUPLICATE`` name resolves to the listed real name with the
      same key; failing that, to the one listed real name whose key starts or ends
      with its key (``REDUNDANT F 250`` -> ``F 250 SD``,
      ``REDUNDANT MACH E`` -> ``MUSTANG MACH E``), provided the key is at least 3
      characters and exactly one name qualifies. Otherwise it resolves to itself:
      never guess.
    """
    by_key: dict[str, list[str]] = {}
    for name in counts:
        if not JUNK_PREFIX.match(name):
            by_key.setdefault(compact_model(name), []).append(name)

    resolved: dict[str, str] = {}
    listed: dict[str, str] = {}  # compact key -> the listed spelling
    for key, names in by_key.items():
        preferred = min(names, key=lambda n: (-counts[n], -n.count(" "), n))
        listed[key] = preferred
        for name in names:
            resolved[name] = preferred

    for name in counts:
        if name in resolved:
            continue
        key = compact_model(JUNK_PREFIX.sub("", name))
        if key in listed:
            resolved[name] = listed[key]
            continue
        candidates = (
            [v for k, v in listed.items() if k.startswith(key) or k.endswith(key)]
            if len(key) >= _MIN_ALIAS_KEY
            else []
        )
        resolved[name] = candidates[0] if len(candidates) == 1 else name
    return resolved


async def model_name_counts(conn: Any, year: int, make: str) -> dict[str, int]:
    """Each distinct indexed model for (year, make), with its distinct bulletin count."""
    rows = await conn.fetch(
        "SELECT model, count(DISTINCT nhtsa_id) AS n FROM comm_products "
        "WHERE model_year = $1 AND make = $2 GROUP BY model",
        int(year),
        str(make).upper(),
    )
    return {r["model"]: r["n"] for r in rows}


async def model_aliases(conn: Any, year: int, make: str, model: str) -> list[str]:
    """Every indexed name that belongs to a vehicle keyed ``model``.

    The names ``resolve_models`` folds into the vehicle's listed name, including
    the model itself. A vehicle whose model resolves to nothing else is matched
    exactly as before: ``[model]``. Sync, preview and the identity check all match
    ``model = ANY(aliases)`` so they agree with the picker.
    """
    model = normalize_model(model)
    resolved = resolve_models(await model_name_counts(conn, year, make))
    target = resolved.get(model, model)
    return sorted({n for n, canonical in resolved.items() if canonical == target} | {model})


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_rows(lines: Iterable[str]) -> Iterator[tuple[str, str, str, int, str]]:
    """Yield hygienic ``(nhtsa_id, make, model, model_year, summary)`` rows.

    Row hygiene, all from JIP-428's measurements:

    * A header row (first column ``TSB/Document ID``) is skipped.
    * Rows where ``Model == Make`` are dropped -- these are the ``BMW|BMW``,
      ``EQUIPMENT|EQUIPMENT`` junk NHTSA files under aftermarket/equipment makers.
    * A comma-packed ``Model Year`` (``"2017,2018"``) is split into one row per
      year, matching the ``comm_products`` grain. ``9999`` (unknown) is kept.
    * ``make`` is uppercased; ``model`` is normalized to the canonical nameplate.
    * Rows missing an id, make, model, or any parseable year are dropped.
    """
    reader = csv.reader(lines)
    for row in reader:
        if len(row) < 5:
            continue
        nhtsa_id = row[0].strip()
        make_raw = row[1].strip()
        model_raw = row[2].strip()
        years_raw = row[3].strip()
        summary = row[4].strip()

        if not nhtsa_id or not make_raw or not model_raw:
            continue
        # Header row.
        if nhtsa_id == CSV_COLUMNS[0]:
            continue
        make = make_raw.upper()
        # Model == Make junk (compare on the raw uppercased values).
        if make == model_raw.upper():
            continue
        model = normalize_model(model_raw)
        if not model:
            continue

        for token in years_raw.split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            yield (nhtsa_id, make, model, int(token), summary)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _window_url(window: str) -> str:
    return f"{BASE_URL}/{_FILENAME.format(window=window)}"


async def discover_windows(client: httpx.AsyncClient) -> list[str]:
    """The authoritative window list from the S3 bucket listing.

    Falls back to ``WINDOWS_FALLBACK`` on any listing failure so ingest still
    runs against the known set when the listing endpoint is unavailable.
    """
    try:
        resp = await client.get(_BUCKET_LISTING, timeout=30.0)
        resp.raise_for_status()
        keys = re.findall(r"MFR_COMMS_RECEIVED_([0-9]{4}-[0-9]{4})\.zip", resp.text)
        windows = sorted(dict.fromkeys(keys))
        if windows:
            return windows
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("bucket listing failed (%s); using the built-in window list", exc)
    return list(WINDOWS_FALLBACK)


def _parse_last_modified(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


async def head_window(
    client: httpx.AsyncClient, window: str
) -> tuple[Optional[str], Optional[datetime]]:
    """Return ``(etag, last_modified)`` for a window without downloading it."""
    resp = await client.head(_window_url(window), timeout=30.0)
    resp.raise_for_status()
    return resp.headers.get("etag"), _parse_last_modified(resp.headers.get("last-modified"))


def _iter_csv_lines(zip_path: str) -> Iterator[str]:
    """Stream decoded text lines from the single CSV inside a downloaded zip.

    Streamed rather than read whole: the largest window is ~144 MB of CSV, and
    holding that plus a per-row Python list in memory is needless when csv can
    consume a line iterator.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"no CSV member in {zip_path}")
        with zf.open(members[0]) as raw:
            # newline="" so csv sees embedded newlines inside quoted summaries
            # correctly; errors="replace" so one bad byte never aborts a window.
            yield from io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")


async def _download_window(
    client: httpx.AsyncClient, window: str, dest
) -> tuple[Optional[str], Optional[datetime]]:
    """Stream a window's zip to ``dest``; return its ``(etag, last_modified)``."""
    async with client.stream("GET", _window_url(window), timeout=180.0) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
            dest.write(chunk)
        dest.flush()
        return resp.headers.get("etag"), _parse_last_modified(resp.headers.get("last-modified"))


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

# Dropped-then-created rather than ON COMMIT DROP: several windows ingest on one
# connection within one process, and a rolled-back test wraps them all in a single
# never-committed transaction where ON COMMIT DROP would never fire, so the second
# window would collide on the name. A leading DROP IF EXISTS is correct in both.
_STAGE_DDL = """
DROP TABLE IF EXISTS _cp_stage;
CREATE TEMP TABLE _cp_stage (
    nhtsa_id   text,
    make       text,
    model      text,
    model_year int,
    summary    text
)
"""

# DISTINCT ON collapses rows duplicated *within* this window (the same product on
# a bulletin listed twice) so the ON CONFLICT clause -- which handles collisions
# with rows another window already owns -- never sees a source row twice, which
# Postgres would otherwise reject.
_MERGE_SQL = """
INSERT INTO comm_products (nhtsa_id, make, model, model_year, summary, source_window)
SELECT DISTINCT ON (nhtsa_id, make, model, model_year)
       nhtsa_id, make, model, model_year, summary, $1
FROM _cp_stage
ON CONFLICT (nhtsa_id, make, model, model_year)
DO UPDATE SET summary = EXCLUDED.summary, source_window = EXCLUDED.source_window
"""

_RECORD_SQL = """
INSERT INTO flatfile_ingests (source_window, etag, last_modified, row_count, ingested_at)
VALUES ($1, $2, $3, $4, now())
ON CONFLICT (source_window) DO UPDATE SET
    etag          = EXCLUDED.etag,
    last_modified = EXCLUDED.last_modified,
    row_count     = EXCLUDED.row_count,
    ingested_at   = now()
"""

_ROWCOUNT_RE = re.compile(r"(\d+)\s*$")


async def ingest_window(
    conn: asyncpg.Connection,
    window: str,
    records: Iterable[tuple[str, str, str, int, str]],
    *,
    etag: str,
    last_modified: Optional[datetime],
) -> int:
    """Replace one window's rows and record its ETag, in a single transaction.

    ``records`` is streamed straight into a temp staging table via COPY, then
    merged into ``comm_products``; nothing about the window is held whole in
    memory. Returns the number of ``comm_products`` rows the merge affected.
    """
    async with conn.transaction():
        await conn.execute(_STAGE_DDL)
        await conn.copy_records_to_table(
            "_cp_stage",
            records=records,
            columns=["nhtsa_id", "make", "model", "model_year", "summary"],
        )
        await conn.execute("DELETE FROM comm_products WHERE source_window = $1", window)
        status = await conn.execute(_MERGE_SQL, window)
        match = _ROWCOUNT_RE.search(status or "")
        row_count = int(match.group(1)) if match else 0
        await conn.execute(_RECORD_SQL, window, etag, last_modified, row_count)
    return row_count


async def _recorded_etags(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT source_window, etag FROM flatfile_ingests")
    return {r["source_window"]: r["etag"] for r in rows}


async def refresh_index(
    conn: asyncpg.Connection,
    client: Optional[httpx.AsyncClient] = None,
    *,
    windows: Optional[Iterable[str]] = None,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """Ingest every window whose S3 ETag changed since its last ingest.

    ``force=True`` re-ingests every window regardless of ETag (the bootstrap).
    Windows are processed sequentially: it keeps memory flat, avoids temp-table
    name races on one connection, and is polite to NHTSA. Returns a per-window
    report: ``{"status": "ingested"|"unchanged"|"error", "rows": int, ...}``.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(headers=_HEADERS, follow_redirects=True)
    report: dict[str, dict[str, Any]] = {}
    try:
        window_list = list(windows) if windows is not None else await discover_windows(client)
        recorded = await _recorded_etags(conn)
        for window in window_list:
            try:
                etag, _ = await head_window(client, window)
                if not force and etag is not None and recorded.get(window) == etag:
                    report[window] = {"status": "unchanged", "etag": etag}
                    log.info("%s: unchanged (etag %s)", window, etag)
                    continue
                with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
                    dl_etag, last_modified = await _download_window(client, window, tmp)
                    rows = await ingest_window(
                        conn,
                        window,
                        parse_rows(_iter_csv_lines(tmp.name)),
                        etag=dl_etag or etag or "",
                        last_modified=last_modified,
                    )
                report[window] = {"status": "ingested", "rows": rows, "etag": dl_etag or etag}
                log.info("%s: ingested %d rows", window, rows)
            except (httpx.HTTPError, zipfile.BadZipFile, ValueError, OSError) as exc:
                # One window failing must not abort the others: the index stays
                # as it was for that window (bounded staleness), which is the
                # whole resilience story -- discovery and sync keep working.
                report[window] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                log.error("%s: ingest failed: %s: %s", window, type(exc).__name__, exc)
    finally:
        if owns_client:
            await client.aclose()
    return report


async def index_is_empty(conn: asyncpg.Connection) -> bool:
    """True when nothing has been ingested yet -- the bootstrap has not run."""
    return not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM comm_products)")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


async def _status(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT source_window, etag, row_count, ingested_at FROM flatfile_ingests "
        "ORDER BY source_window"
    )
    if not rows:
        print("No windows ingested yet. Run --bootstrap.")
        return
    total = 0
    for r in rows:
        total += r["row_count"]
        when = f"{r['ingested_at']:%Y-%m-%d %H:%M}"
        print(f"  {r['source_window']}  {r['row_count']:>9,} rows  ingested {when}")
    products = await conn.fetchval("SELECT count(*) FROM comm_products")
    print(f"  {'total':<9}  {total:>9,} rows recorded  |  {products:,} comm_products rows")


async def _run(args: argparse.Namespace) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if args.status:
            await _status(conn)
            return 0
        windows = [args.window] if args.window else None
        report = await refresh_index(conn, windows=windows, force=args.bootstrap)
    ingested = sum(1 for r in report.values() if r["status"] == "ingested")
    errors = [w for w, r in report.items() if r["status"] == "error"]
    total_rows = sum(r.get("rows", 0) for r in report.values())
    print(f"Windows ingested: {ingested}/{len(report)}  |  rows: {total_rows:,}")
    for w, r in sorted(report.items()):
        detail = (
            r.get("error") or f"{r.get('rows', 0):,} rows"
            if r["status"] != "unchanged"
            else "unchanged"
        )
        print(f"  {w}: {r['status']}  {detail if r['status'] != 'unchanged' else ''}".rstrip())
    if errors:
        print(f"FAILED windows: {', '.join(errors)}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest NHTSA manufacturer-communications flat files."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bootstrap", action="store_true", help="ingest all windows (ignore ETags)")
    group.add_argument(
        "--refresh", action="store_true", help="re-ingest only windows whose ETag changed"
    )
    group.add_argument("--status", action="store_true", help="show per-window ingest state")
    parser.add_argument(
        "--window", help="restrict --bootstrap/--refresh to a single window, e.g. 2020-2024"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    async def _main() -> int:
        try:
            return await _run(args)
        finally:
            await close_pool()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
