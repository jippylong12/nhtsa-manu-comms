# ADR 0001 — Re-key vehicle discovery and comms fetching on the plain nameplate

- **Status:** Accepted (M2 of Linear project "NHTSA Comms — Plain-Nameplate Re-Key")
- **Date:** 2026-07-24
- **Evidence:** Linear JIP-427 (endpoint inventory), JIP-428 (vocabulary), JIP-429 (superset/de-dup)

## Context

The Add-Vehicle picker navigates NHTSA's crash-test taxonomy (`/vehicles/*`), which splits
every nameplate into body-style "models" (`F-150 SUPER CREW`) and requires resolving a
numeric `vehicleId` via `/vehicles/byYmmt` — a route NHTSA's gateway hard-kills at 30s and
that 504s for high-variant nameplates. But manufacturer communications are keyed on the
plain nameplate (`F-150`): production holds 1,627 F-150 associations and **zero** body-style
ones. We navigate the wrong taxonomy to reach the data.

The M1 spike found the decisive fact: **NHTSA publishes the entire manufacturer-communications
corpus as official flat files** on `static.nhtsa.gov`, refreshed daily, keyed exactly
`(NHTSA ID, Make, Model, Model Year)` in plain-nameplate vocabulary, with no API gateway in
the path. NHTSA's own datasets page offers *no* by-vehicle API for manufacturer
communications (only "Download Data") — and NHTSA's own consumer site uses the same
`byYmmt` chain we do and was observed broken by the same 504s.

## Decision

**Replace the per-vehicle NHTSA API discovery chain with a flat-file-backed index in
Postgres, and key vehicle identity on `(year, make, model)` where `model` is the plain
nameplate.** `byYmmt`, `/vehicles/{id}/details`, and the `vehicle_catalog` cache are
**deleted entirely** — not kept as fallback. Per-communication detail (payload + PDF list)
continues to come from `/safetyIssues/byNhtsaId`, unchanged.

### 1. Vehicle identity

- `vehicles.id` (bigserial PK) remains the internal identity; `communication_vehicles` is
  structurally untouched.
- The **domain identity** becomes `(year, make, model)`, plain nameplate, uppercase,
  enforced by a partial unique index:
  `CREATE UNIQUE INDEX ... ON vehicles (year, make, model) WHERE active;`
- `nhtsa_vehicle_id` becomes **nullable legacy data** (kept for rollback and history; no
  code reads it after M3). New vehicles insert NULL. The `UNIQUE NOT NULL` constraint is
  dropped.
- `vehicles.trim` (`PU/RC`, `2 DR`) is meaningless under the new taxonomy: **dropped** in
  the M4 migration, along with its create/update plumbing.
- **Wire identity changes from `nhtsa_vehicle_id` to `vehicles.id`.** Today every API route
  (`/api/vehicles/{vehicle_id}`, refresh preview/start/status) uses `nhtsa_vehicle_id` as
  `vehicleId`. After re-key there is no NHTSA vehicle id to use. The app is its own only
  consumer, and backend + frontend ship together in this project, so the clean switch is
  correct — no compatibility shim. `VehicleCreate` loses `vehicle_id` (required today) and
  gains required `make`; the response's `vehicleId` field becomes the internal id.

### 2. Ingest: the flat-file index

New table (replaces `vehicle_catalog`'s role — that table is dropped in M4):

```sql
CREATE TABLE comm_products (
    nhtsa_id   text NOT NULL,          -- joins to communications.nhtsa_id
    make       text NOT NULL,          -- uppercase, as published
    model      text NOT NULL,          -- normalized plain nameplate (see §5)
    model_year int  NOT NULL,          -- 9999 = unknown, as published
    summary    text,                   -- concise summary from the CSV
    window     text NOT NULL,          -- source file, e.g. '2020-2024'
    PRIMARY KEY (nhtsa_id, make, model, model_year)
);
CREATE INDEX comm_products_ymm ON comm_products (model_year, make, model);

CREATE TABLE flatfile_ingests (
    window        text PRIMARY KEY,    -- '1995-1999' … '2025-2026'
    etag          text NOT NULL,       -- S3 ETag of the ingested zip
    last_modified timestamptz,
    row_count     integer NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now()
);
```

- **Source:** `https://static.nhtsa.gov/odi/ffdd/tsbs/MFR_COMMS_RECEIVED_<window>.zip`
  (CSV variant: `TSB/Document ID` [= NHTSA ID], `Make`, `Model`, `Model Year`,
  `Concise Summary`). Schema doc: `.../tsbs/TSBS.txt`. Largest window ≈ 12 MB zip /
  144 MB CSV — trivial for a daily job.
- **Bootstrap:** ingest all 8 windows once (M3). Full corpus ≈ a few million skinny rows.
- **Refresh:** a step at the head of the existing refresh run (and/or the nightly pipeline):
  HEAD each window, compare S3 ETag against `flatfile_ingests.etag`, re-ingest only changed
  windows (NHTSA re-stamps `last-modified` on every window daily, so ETag — not date — is
  the change signal). Per-window re-ingest is delete-and-insert inside one transaction.
- **Row hygiene (from JIP-428):** drop rows where `Model == Make` (`BMW|BMW`,
  `HONDA|HONDA` junk); split comma-packed `Model Year` values (`"2017,2018"`) into one row
  per year; normalize model per §5.

### 3. Discovery path (the picker)

All three dropdowns are served **from Postgres, zero NHTSA calls in the interactive path**:

- years: `SELECT DISTINCT model_year FROM comm_products WHERE model_year <> 9999 ORDER BY 1 DESC`
  (bounded to the last ~30 for the UI);
- makes: distinct makes for the year, intersected with the existing `TOP_MAKES` list for
  curation (full list behind a "show all" if wanted later);
- models: `SELECT DISTINCT model FROM comm_products WHERE model_year=$1 AND make=$2 ORDER BY 1`.

`GET /api/communications/discovery/variants` and `/discovery/trims` are **removed** (the
frontend loses its resolve step in M5). Discovery answers in milliseconds; the "Finding this
vehicle…" state and its 503 graceful-degradation path become moot — the picker can no longer
be blocked by NHTSA at all. Variant nameplates NHTSA keys separately (`ACCORD HYBRID`,
`CIVIC TYPE R`) appear as their own picker entries; association is **exact match** on the
picked nameplate (no family matching — JIP-428 edge case 1).

### 4. Fetch path (sync/refresh)

`src/jobs/sync.py::sync_vehicle` changes its discovery step only:

- **Today:** `get_vehicle_details(nhtsa_vehicle_id)` → parse `manufacturerCommunications` →
  comm id list.
- **After:** `SELECT nhtsa_id, summary FROM comm_products WHERE model_year=$1 AND make=$2
  AND model=$3` → comm id list. (The id→summary / id→comm-number maps sync builds from the
  details payload are replaced by `comm_products.summary` and by fields already present in
  the `byNhtsaId` payload.)
- Everything downstream is unchanged: `existing_nhtsa_ids` dedup, per-comm
  `get_safety_issue(nhtsa_id)` (retry/backoff, semaphore, 0.2s pacing), pending rows,
  `comm_documents`, `communication_vehicles` links, extract → LLM → embed stages,
  `refresh_runs` progress/failure recording.
- Refresh `preview()` counts new bulletins from `comm_products` instead of a live
  `get_vehicle_details` call — the preview also stops being able to 504.

`NHTSAClient` after M3 keeps: `get_safety_issue`, `fetch_communications_batch`. Deleted:
`get_vehicle_details`, `get_vehicle_variants` (byYmmt), `get_models_for_make_year`,
`get_trims_for_model`, `get_makes_for_year`, `get_model_years`, and the three
`extract_*_from_details` helpers. A new `flatfile.py` module owns download/parse/ingest.

### 5. Normalization (from JIP-428, measured 98–100% on cars across 7 make/years)

Canonical form for `comm_products.model` and all matching: uppercase → hyphen→space →
collapse whitespace. A **compact form** (strip all spaces and hyphens) is the fallback
matcher used by the M4 backfill (`CMAX`≈`C-MAX`, `MACH-E`≈`MACH E`, `4500 HD`≈`4500HD`).
Granular→plain mapping (longest-prefix against the flat vocabulary) exists **only in the M4
backfill script**, never at runtime.

### 6. Caching & resilience

- The flat-file index **is** the cache; it serves discovery and sync even when NHTSA is
  fully down. A failed daily ETag check leaves the previous index in place (staleness is
  bounded by `flatfile_ingests.ingested_at`, surfaced in logs; a follow-up could expose it
  in the UI).
- `static.nhtsa.gov` (S3/CloudFront) was fully healthy throughout the very API-gateway
  outage that motivated this project — the two failure domains are independent.
- `get_safety_issue` retry/backoff is unchanged; per-comm failures already record on
  `refresh_runs` and surface in the UI. That behavior is preserved verbatim.
- The shipped 503 graceful degradation (`/discovery/variants`) is **made moot** and removed
  with the endpoint; its regression test (`backend/tests/test_variants_lookup.py`) retires
  with it, replaced by tests in §8. No infinite-spinner state survives anywhere: the picker
  cannot block on NHTSA, and refresh failures fail fast into `refresh_runs.error`.
- Healthy-window follow-up (non-blocking, from JIP-427): re-probe bare
  `/manufacturerCommunications` semantics once NHTSA recovers; curiosity only — nothing in
  this design depends on it.

### 7. Migration plan (M4, `backend/migrations/0007_plain_nameplate.sql` + backfill script)

Order matters; each step is transactional:

1. **Schema (additive):** create `comm_products` + `flatfile_ingests`; drop the
   `vehicles.nhtsa_vehicle_id` NOT NULL/UNIQUE constraints (column stays, nullable).
2. **Ingest** all flat-file windows (M3 code path).
3. **Backfill vehicles:** for each row, map `model` → plain nameplate by longest-prefix
   match (with compact fallback) against the flat vocabulary for that `(year, make)`.
   Current production (verified in JIP-429): `F-150 REGULAR CAB`→`F-150`; the three EVs and
   the Civic are already plain; **BMW `3 SERIES SEDAN` (id=1147) has no 1:1 map** — the
   backfill flags it (report output), tentatively assigning `330I`, and Marcus confirms or
   re-picks in the UI. `trim` values are recorded in the migration report, then the column
   is dropped.
4. **Merge collisions** per the JIP-429 rule: group by `(year, make, plain model)`; survivor
   = lowest id; repoint `communication_vehicles` with `ON CONFLICT (communication_id,
   vehicle_id) DO NOTHING`-style dedup keeping `MIN(first_seen_at)` and unioned
   `matched_keywords` (all `[]` in production — vestigial); `active = bool_or(active)`;
   delete absorbed rows last. **On current production data this is a no-op (six vehicles,
   six distinct identities, zero collisions).**
5. **Constraints:** add the partial unique index on `(year, make, model) WHERE active`;
   drop `vehicle_catalog`.
6. **Re-associate:** run the new sync per vehicle so `communication_vehicles` picks up the
   flat-file superset (JIP-429 measured gains: +1/+2/+1/+160/+27/+1; notably the 2020 Civic
   goes 0 → 160 — the old path had silently lost it entirely).
7. **Dry-run mode first:** the backfill script prints the full plan (mappings, merges,
   counts) without writing; then applies against a copy of production; then production.

**Rollback:** steps 1–2 are additive; the backfill keeps `nhtsa_vehicle_id` values and a
migration report of every mutation, so restoring the old constraints + git-reverting the
code returns the system to the pre-migration state. Absorbed-vehicle rows (none expected
today) are the only destructive change; the report captures them.

### 8. Test plan sketch

- **M3:** unit tests for flat-file parse/normalization (comma years, 9999, junk rows,
  hyphen/space variants); ingest idempotency (same ETag → skip, changed ETag → replace);
  discovery endpoints return plain nameplates from a seeded index; sync associates via
  `comm_products` with mocked `byNhtsaId`; refresh failure under simulated NHTSA outage
  still records `refresh_runs.error` fast.
- **M4:** migration applies on a production copy; dry-run report matches applied changes;
  FK-integrity query proves zero orphaned `communication_vehicles`; per-vehicle comm counts
  ≥ pre-migration baseline (JIP-429 script re-run, saved as a repeatable check).
- **M6:** re-run the superset script against migrated production; full backend pytest +
  frontend lint/build; live end-to-end add of `F-150`/`BRONCO` timed (no step near 30s);
  blocked-network failure-mode check (fast honest errors, no spinner).

## Consequences

- The 30s-timeout class of failure is removed rather than mitigated; the picker becomes
  Postgres-fast and lists exactly the models that have communications.
- Comms coverage strictly grows (superset proven per vehicle); one tracked vehicle's total
  silent data loss is repaired as a side effect.
- New operational dependency: the daily flat-file ETag check (cheap, and degradation is
  graceful staleness, not breakage).
- `docs/schema.md` and the architecture notes in `CLAUDE.md` need updating in M6 to
  describe `comm_products`/`flatfile_ingests` and the removal of `vehicle_catalog`.
- The wire-identity switch (`vehicleId` = internal id) is a breaking API change shipped
  atomically with the frontend in M5.
