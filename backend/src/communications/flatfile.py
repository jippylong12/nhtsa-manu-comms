"""The flat-file communications index: download, parse, scan (ADR 0001, lean index).

NHTSA publishes the whole manufacturer-communications corpus as daily-refreshed
flat files on static.nhtsa.gov (S3/CloudFront -- no API gateway in the path),
keyed exactly on ``(NHTSA ID, Make, Model, Model Year)`` in plain-nameplate
vocabulary. This module turns those files into the two small tables the app reads,
instead of mirroring the whole catalogue:

* ``comm_vocab`` -- every (model year, make, model) a window lists, with its
  distinct-bulletin count. The add-vehicle picker reads it, and the counts drive
  ``resolve_models``' alias folding.
* ``vehicle_bulletins`` -- the bulletins of each *tracked* vehicle, found by
  scanning the window for the vehicle's identity and its folded aliases. Sync and
  the refresh preview read it.
* ``vehicle_scans`` -- which (vehicle, window) pairs were scanned at which etag.

Layout of the source (see ``.../tsbs/TSBS.txt``): eight ``MFR_COMMS_RECEIVED_*.zip``
files, one per 5-year window of *date received by NHTSA*. Each zip holds one CSV,
one record per ``(TSB/Document ID, Make, Model, Model Year)`` -- so a bulletin
covering five products is five rows. The CSV's first column is labelled
"TSB/Document ID" but its *values* are the NHTSA ID Number that joins to
``communications.nhtsa_id`` (verified against production); the manufacturer's own
document id lives only inside the summary text.

One pass per window, in one transaction: stream the CSV into a temp stage, rebuild
that window's ``comm_vocab`` rows, rescan every vehicle that needs it, record the
etag. NHTSA re-stamps every window's ``Last-Modified`` daily even when nothing
changed, so the S3 ETag is the change signal. A window is touched when its etag
differs from the last ingest *or* a vehicle has no scan at the current etag (a newly
tracked vehicle, or one whose scan never ran), so adding a vehicle needs no download
of anything but the windows it has not seen. Downloaded zips are cached in
``backend/.flatfile_cache/<window>.<etag>.zip`` (override with FLATFILE_CACHE_DIR),
so a rescan of an unchanged window reads the file instead of downloading it.

Usage:
    python -m src.communications.flatfile --bootstrap          # rebuild every window, scan all vehicles
    python -m src.communications.flatfile --refresh            # repair stale windows and scans
    python -m src.communications.flatfile --scan --vehicle 7   # scan one vehicle against every window
    python -m src.communications.flatfile --status             # windows, vehicles, sizes
"""

import argparse
import asyncio
import csv
import hashlib
import io
import logging
import os
import re
import secrets
import sys
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Optional

import asyncpg
import httpx

from src.db import close_pool, get_pool
from src.jobs import repository as repo

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

    This is the form stored in ``comm_vocab.model`` and the form every match
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

    ``counts`` is each distinct ``comm_vocab.model`` with its distinct bulletin
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
    """Each distinct indexed model for (year, make), with its bulletin count.

    The vocabulary keeps one count per window; a bulletin listed in two windows is
    counted in each, which only matters to the tie-break between spellings.
    """
    rows = await conn.fetch(
        "SELECT model, sum(bulletins) AS n FROM comm_vocab "
        "WHERE model_year = $1 AND make = $2 GROUP BY model",
        int(year),
        str(make).upper(),
    )
    return {r["model"]: r["n"] for r in rows}


async def model_aliases(conn: Any, year: int, make: str, model: str) -> list[str]:
    """Every indexed name that belongs to a vehicle keyed ``model``.

    The names ``resolve_models`` folds into the vehicle's listed name, including
    the model itself. A vehicle whose model resolves to nothing else is matched
    exactly as before: ``[model]``. The scan matches ``model = ANY(aliases)`` when it
    fills ``vehicle_bulletins``, so sync, preview and the identity check agree with
    the picker.
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
      year, matching the one-row-per-product grain of the file. ``9999`` (unknown) is kept.
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
# Zip cache: backend/.flatfile_cache/<window>.<etag>.zip
# --------------------------------------------------------------------------

# backend/.flatfile_cache (this file is backend/src/communications/flatfile.py).
CACHE_DIR = Path(__file__).resolve().parents[2] / ".flatfile_cache"


def _cache_dir() -> Path:
    """The cache directory; FLATFILE_CACHE_DIR is read per call so tests can redirect it."""
    override = os.environ.get("FLATFILE_CACHE_DIR")
    return Path(override) if override else CACHE_DIR


def _safe_etag(etag: Optional[str]) -> str:
    """The etag as a file-name part: S3's quotes and any ``/`` are dropped."""
    return re.sub(r"[^A-Za-z0-9._-]", "", etag or "")


def _cached_zip(window: str, etag: Optional[str]) -> Optional[Path]:
    """The cached zip for exactly this window and etag, or None."""
    safe = _safe_etag(etag)
    if not safe:
        return None
    path = _cache_dir() / f"{window}.{safe}.zip"
    return path if path.is_file() else None


def _store_zip(window: str, etag: Optional[str], part: Path) -> Path:
    """Move a freshly downloaded zip into the cache and drop the window's older ones.

    Returns the path to read from: the cache file, or ``part`` itself when the etag
    is unknown (nothing to key a cache entry on).
    """
    safe = _safe_etag(etag)
    if not safe:
        return part
    final = part.with_name(f"{window}.{safe}.zip")
    part.replace(final)
    for other in final.parent.glob(f"{window}.*.zip"):
        if other != final:
            other.unlink(missing_ok=True)
    return final


# --------------------------------------------------------------------------
# Ingest and scan
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

# One count per (year, make, model) triple: the distinct bulletins it lists.
_VOCAB_SQL = """
INSERT INTO comm_vocab (model_year, make, model, source_window, bulletins)
SELECT model_year, make, model, $1::text, count(DISTINCT nhtsa_id)
FROM _cp_stage
GROUP BY model_year, make, model
"""

# A vehicle's bulletins in this window: its own spelling and every folded alias,
# one row per bulletin. DISTINCT ON collapses a bulletin filed under two spellings,
# keeping the vehicle's own spelling's summary when it has one.
_SCAN_SQL = """
INSERT INTO vehicle_bulletins (vehicle_id, nhtsa_id, summary, source_window)
SELECT DISTINCT ON (nhtsa_id) $1::bigint, nhtsa_id, coalesce(summary, ''), $2::text
FROM _cp_stage
WHERE model_year = $3 AND make = $4 AND model = ANY($5::text[])
ORDER BY nhtsa_id, model = $6 DESC, model
ON CONFLICT (vehicle_id, nhtsa_id)
DO UPDATE SET summary = EXCLUDED.summary, source_window = EXCLUDED.source_window
"""

_SCANNED_SQL = """
INSERT INTO vehicle_scans (vehicle_id, source_window, etag, scanned_at)
VALUES ($1, $2, $3, now())
ON CONFLICT (vehicle_id, source_window)
DO UPDATE SET etag = EXCLUDED.etag, scanned_at = now()
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


def _window_lock_key(window: str) -> int:
    """A stable signed 64-bit advisory-lock key for one window."""
    digest = hashlib.sha256(f"flatfile:{window}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _affected(status: Optional[str]) -> int:
    """Row count out of an asyncpg command tag (``INSERT 0 5``, ``COPY 3``)."""
    match = _ROWCOUNT_RE.search(status or "")
    return int(match.group(1)) if match else 0


async def ingest_window(
    conn: asyncpg.Connection,
    window: str,
    records: Iterable[tuple[str, str, str, int, str]],
    *,
    etag: str,
    last_modified: Optional[datetime],
    vehicles: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """One pass over a window, in a single transaction.

    ``records`` is streamed into a temp stage via COPY (nothing about the window is
    held whole in memory) and indexed on ``(model_year, make, model)``. Then the
    window's ``comm_vocab`` rows are rebuilt, and each of ``vehicles`` (dicts with
    ``id, year, make, model``) has its ``vehicle_bulletins`` for this window replaced
    -- so a bulletin NHTSA withdrew disappears -- with the vehicle's aliases folded in
    from the vocabulary just written, and its scan recorded at ``etag``.

    Returns ``{"rows": stage rows, "vocab_rows": n, "per_vehicle": {id: bulletins}}``.
    """
    per_vehicle: dict[int, int] = {}
    async with conn.transaction():
        # Passes over one window from different connections (the scan on add, a
        # refresh run, the nightly) take turns: two concurrent rebuilds of the
        # window's comm_vocab rows would otherwise collide on its primary key.
        await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", _window_lock_key(window))
        await conn.execute(_STAGE_DDL)
        rows = _affected(
            await conn.copy_records_to_table(
                "_cp_stage",
                records=records,
                columns=["nhtsa_id", "make", "model", "model_year", "summary"],
            )
        )
        await conn.execute("CREATE INDEX ON _cp_stage (model_year, make, model)")
        await conn.execute("ANALYZE _cp_stage")

        await conn.execute("DELETE FROM comm_vocab WHERE source_window = $1", window)
        vocab_rows = _affected(await conn.execute(_VOCAB_SQL, window))

        for vehicle in vehicles:
            own = normalize_model(vehicle["model"])
            make = str(vehicle["make"]).upper()
            aliases = await model_aliases(conn, int(vehicle["year"]), make, vehicle["model"])
            await conn.execute(
                "DELETE FROM vehicle_bulletins WHERE vehicle_id = $1 AND source_window = $2",
                vehicle["id"],
                window,
            )
            per_vehicle[vehicle["id"]] = _affected(
                await conn.execute(
                    _SCAN_SQL, vehicle["id"], window, int(vehicle["year"]), make, aliases, own
                )
            )
            await conn.execute(_SCANNED_SQL, vehicle["id"], window, etag)

        await conn.execute(_RECORD_SQL, window, etag, last_modified, rows)
    return {"rows": rows, "vocab_rows": vocab_rows, "per_vehicle": per_vehicle}


async def _recorded_etags(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT source_window, etag FROM flatfile_ingests")
    return {r["source_window"]: r["etag"] for r in rows}


async def _scope(conn: asyncpg.Connection, vehicles: Literal["all"] | list[int]) -> list[dict]:
    """The active tracked vehicles a refresh covers: all of them, or just these ids."""
    tracked = await repo.tracked_vehicles(conn)
    if vehicles != "all":
        wanted = set(vehicles)
        tracked = [v for v in tracked if v["id"] in wanted]
    return [{k: v[k] for k in ("id", "year", "make", "model")} for v in tracked]


async def _window_zip(
    client: httpx.AsyncClient, window: str, etag: Optional[str]
) -> tuple[Path, Optional[str], Optional[datetime], bool]:
    """The zip for a window: the cached file for ``etag``, else a fresh download.

    Returns ``(path, etag, last_modified, temporary)``. ``last_modified`` is None on a
    cache hit (the caller has the HEAD's). ``temporary`` is True when the path is not
    in the cache and the caller must delete it.
    """
    cached = _cached_zip(window, etag)
    if cached is not None:
        return cached, etag, None, False
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    # A name of its own per download: two scans downloading the same window at once
    # (scan on add and a refresh run) must not write into, or rename away, one file.
    part = cache / f"{window}.{os.getpid()}.{secrets.token_hex(4)}.part"
    try:
        with open(part, "wb") as dest:
            dl_etag, last_modified = await _download_window(client, window, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    used = dl_etag or etag
    path = _store_zip(window, used, part)
    return path, used, last_modified, path == part


async def refresh_index(
    conn: asyncpg.Connection,
    client: Optional[httpx.AsyncClient] = None,
    *,
    windows: Optional[Iterable[str]] = None,
    vehicles: Literal["all"] | list[int] = "all",
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """Repair every stale window, and every stale (vehicle, window) scan.

    A window is *stale* for a vehicle when ``vehicle_scans`` has no row for the pair
    or the row's etag differs from the window's current S3 etag. A window whose etag
    differs from ``flatfile_ingests`` is re-ingested for every vehicle in scope; one
    whose etag is unchanged is re-read only for the vehicles that are stale, and is
    skipped when there are none. ``force=True`` re-ingests every window for every
    vehicle in scope regardless (the bootstrap).

    Windows are processed sequentially: it keeps memory flat, avoids temp-table name
    races on one connection, and is polite to NHTSA. Returns a per-window report:
    ``{"status": "ingested"|"unchanged"|"error", "rows": int, "scanned": [vehicle ids]}``.
    One window failing never aborts the others.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(headers=_HEADERS, follow_redirects=True)
    report: dict[str, dict[str, Any]] = {}
    try:
        window_list = list(windows) if windows is not None else await discover_windows(client)
        recorded = await _recorded_etags(conn)
        scope = await _scope(conn, vehicles)
        for window in window_list:
            try:
                etag, head_modified = await head_window(client, window)
                scans = {
                    r["vehicle_id"]: r["etag"]
                    for r in await conn.fetch(
                        "SELECT vehicle_id, etag FROM vehicle_scans "
                        "WHERE source_window = $1 AND vehicle_id = ANY($2::bigint[])",
                        window,
                        [v["id"] for v in scope],
                    )
                }
                stale = [v for v in scope if scans.get(v["id"]) != etag]
                changed = force or etag is None or recorded.get(window) != etag
                if not changed and not stale:
                    report[window] = {"status": "unchanged", "etag": etag, "rows": 0, "scanned": []}
                    log.info("%s: unchanged (etag %s)", window, etag)
                    continue
                to_scan = scope if changed else stale
                path, used_etag, downloaded_modified, temporary = await _window_zip(
                    client, window, etag
                )
                try:
                    result = await ingest_window(
                        conn,
                        window,
                        parse_rows(_iter_csv_lines(str(path))),
                        etag=used_etag or "",
                        last_modified=downloaded_modified or head_modified,
                        vehicles=to_scan,
                    )
                finally:
                    if temporary:
                        path.unlink(missing_ok=True)
                recorded[window] = used_etag or ""
                report[window] = {
                    "status": "ingested",
                    "rows": result["rows"],
                    "etag": used_etag,
                    "scanned": [v["id"] for v in to_scan],
                }
                log.info(
                    "%s: ingested %d rows, scanned %d vehicle(s)",
                    window,
                    result["rows"],
                    len(to_scan),
                )
            except (httpx.HTTPError, zipfile.BadZipFile, ValueError, OSError) as exc:
                # One window failing must not abort the others: the index stays
                # as it was for that window (bounded staleness), which is the
                # whole resilience story -- discovery and sync keep working.
                report[window] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "scanned": [],
                }
                log.error("%s: ingest failed: %s: %s", window, type(exc).__name__, exc)
    finally:
        if owns_client:
            await client.aclose()
    return report


async def scan_vehicle(conn: asyncpg.Connection, vehicle_id: int) -> dict[str, dict[str, Any]]:
    """Scan one vehicle against every window it is stale for (all, for a new vehicle)."""
    return await refresh_index(conn, vehicles=[vehicle_id])


async def scan_vehicle_background(vehicle_id: int) -> None:
    """Scan one vehicle without blocking or failing the caller (run on add).

    A failed scan is repaired by the next refresh or nightly run, so every
    exception is swallowed with a warning.
    """
    try:
        from src.db import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            report = await refresh_index(conn, vehicles=[vehicle_id])
        log.info("background scan of vehicle %s: %s", vehicle_id, report)
    except Exception as exc:  # noqa: BLE001 - never surface to the add request
        log.warning(
            "background scan of vehicle %s failed: %s: %s", vehicle_id, type(exc).__name__, exc
        )


async def vehicle_is_scanned(conn: asyncpg.Connection, vehicle_id: int) -> bool:
    """True once the vehicle has been scanned against at least one window."""
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM vehicle_scans WHERE vehicle_id = $1)", vehicle_id
        )
    )


async def vehicle_fully_scanned(conn: asyncpg.Connection, vehicle_id: int) -> bool:
    """True when the vehicle has a current-etag scan row for every ingested window.

    "Scanned at all" is not enough: a scan on add writes one window at a time, so a
    vehicle whose first scan stopped partway has a bulletin list missing whole windows.
    Treating it as scanned would let a refresh whose missing window fails again report
    "up to date". With nothing ingested yet (no bootstrap) there is no window to be
    missing, so any scan row counts.
    """
    ingested = await conn.fetchval("SELECT count(*) FROM flatfile_ingests")
    if not ingested:
        return await vehicle_is_scanned(conn, vehicle_id)
    missing = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM flatfile_ingests i
          WHERE NOT EXISTS (
            SELECT 1 FROM vehicle_scans s
            WHERE s.vehicle_id = $1
              AND s.source_window = i.source_window
              AND s.etag = i.etag
          )
        )
        """,
        vehicle_id,
    )
    return not missing


async def index_is_empty(conn: asyncpg.Connection) -> bool:
    """True when nothing has been ingested yet -- the bootstrap has not run."""
    return not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM comm_vocab)")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.glob("*") if f.is_file()) if path.is_dir() else 0


async def _status(conn: asyncpg.Connection) -> None:
    windows = await conn.fetch(
        "SELECT i.source_window, i.etag, i.ingested_at, "
        "(SELECT count(*) FROM comm_vocab c WHERE c.source_window = i.source_window) AS vocab "
        "FROM flatfile_ingests i ORDER BY i.source_window"
    )
    if not windows:
        print("No windows ingested yet. Run --bootstrap.")
    for r in windows:
        when = f"{r['ingested_at']:%Y-%m-%d %H:%M}"
        print(f"  {r['source_window']}  etag {r['etag']}  {r['vocab']:>7,} vocab rows  ingested {when}")
    vehicles = await conn.fetch(
        "SELECT v.id, v.year, v.make, v.model, "
        "(SELECT count(*) FROM vehicle_bulletins b WHERE b.vehicle_id = v.id) AS bulletins, "
        "array(SELECT i.source_window FROM flatfile_ingests i "
        "      LEFT JOIN vehicle_scans s ON s.vehicle_id = v.id AND s.source_window = i.source_window "
        "      WHERE s.etag IS DISTINCT FROM i.etag ORDER BY i.source_window) AS stale "
        "FROM vehicles v WHERE v.active ORDER BY v.id"
    )
    for v in vehicles:
        stale = ", ".join(v["stale"]) or "none"
        print(
            f"  vehicle {v['id']:<5} {v['year']} {v['make']} {v['model']}: "
            f"{v['bulletins']:,} bulletins  |  stale windows: {stale}"
        )
    sizes = await conn.fetchrow(
        "SELECT pg_size_pretty(pg_total_relation_size('comm_vocab')) AS vocab, "
        "pg_size_pretty(pg_total_relation_size('vehicle_bulletins')) AS bulletins"
    )
    cache = _cache_dir()
    print(
        f"  sizes: comm_vocab {sizes['vocab']}, vehicle_bulletins {sizes['bulletins']}, "
        f"zip cache {_dir_size(cache) / 1e6:,.1f} MB ({cache})"
    )


async def _run(args: argparse.Namespace) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if args.status:
            await _status(conn)
            return 0
        windows = [args.window] if args.window else None
        if args.scan:
            report = await refresh_index(conn, windows=windows, vehicles=[args.vehicle])
        else:
            report = await refresh_index(conn, windows=windows, force=args.bootstrap)
    ingested = sum(1 for r in report.values() if r["status"] == "ingested")
    errors = [w for w, r in report.items() if r["status"] == "error"]
    total_rows = sum(r.get("rows", 0) for r in report.values())
    print(f"Windows ingested: {ingested}/{len(report)}  |  rows: {total_rows:,}")
    for w, r in sorted(report.items()):
        if r["status"] == "unchanged":
            print(f"  {w}: unchanged")
        elif r["status"] == "error":
            print(f"  {w}: error  {r.get('error')}")
        else:
            print(f"  {w}: ingested  {r.get('rows', 0):,} rows, {len(r['scanned'])} vehicle(s) scanned")
    if errors:
        print(f"FAILED windows: {', '.join(errors)}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest NHTSA manufacturer-communications flat files."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--bootstrap",
        action="store_true",
        help="rebuild every window and scan every tracked vehicle (ignore ETags)",
    )
    group.add_argument(
        "--refresh",
        action="store_true",
        help="repair stale windows and stale vehicle scans",
    )
    group.add_argument(
        "--scan", action="store_true", help="scan one tracked vehicle (needs --vehicle N)"
    )
    group.add_argument(
        "--status", action="store_true", help="show windows, vehicle scans, table and cache sizes"
    )
    parser.add_argument("--vehicle", type=int, help="the vehicle id for --scan")
    parser.add_argument(
        "--window", help="restrict --bootstrap/--refresh/--scan to a single window, e.g. 2020-2024"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.scan and args.vehicle is None:
        parser.error("--scan needs --vehicle N")

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
