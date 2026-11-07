-- 0007_comm_products.sql
-- The flat-file communications index (ADR 0001, milestone M3).
--
-- NHTSA publishes the entire manufacturer-communications corpus as daily flat
-- files on static.nhtsa.gov, keyed exactly (NHTSA ID, Make, Model, Model Year)
-- in plain-nameplate vocabulary, with no API gateway in the path. This table is
-- that corpus, ingested into Postgres. It replaces vehicle_catalog's role as the
-- thing the add-vehicle picker reads from and the thing sync uses to learn which
-- communications belong to a vehicle -- but keyed on the plain nameplate the
-- comms are actually filed under (F 150), not the crash-test body styles
-- (F-150 SUPER CREW) the byYmmt lookup forced on us.
--
-- Additive only. vehicle_catalog stays until M4 drops it; the vehicles-table
-- re-key (nullable nhtsa_vehicle_id, dropped trim, the (year,make,model) unique
-- index) is also M4. M3 code stops reading vehicle_catalog but leaves the table.

-- One row per (communication, product) the flat file lists. A single bulletin
-- applies to many products, so a single nhtsa_id appears once per product it
-- covers -- that is exactly the grain the picker and sync both want.
CREATE TABLE IF NOT EXISTS comm_products (
    -- Joins to communications.nhtsa_id (text there, text here). This is the
    -- flat file's "TSB/Document ID" column, whose *values* are the NHTSA ID
    -- Number despite the column name -- verified against production: all 20
    -- sampled F-150 nhtsa_ids appear here.
    nhtsa_id   text NOT NULL,
    make       text NOT NULL,          -- uppercase, as published
    model      text NOT NULL,          -- normalized plain nameplate (see flatfile.normalize_model)
    model_year int  NOT NULL,          -- 9999 = unknown, as published
    summary    text,                   -- concise summary from the CSV
    -- Source file span, e.g. '2020-2024' (provenance; used to scope per-window
    -- re-ingest). Named source_window rather than the ADR's `window` because
    -- WINDOW is a reserved word in Postgres and would need quoting everywhere.
    source_window text NOT NULL,
    PRIMARY KEY (nhtsa_id, make, model, model_year)
);

-- The one lookup discovery and sync both make: models for a year/make, and the
-- comm-id list for a (year, make, model).
CREATE INDEX IF NOT EXISTS comm_products_ymm_idx
    ON comm_products (model_year, make, model);

-- The ETag ledger that makes the daily refresh idempotent: HEAD each window,
-- re-ingest only the ones whose S3 ETag changed. NHTSA re-stamps last-modified
-- on every window daily, so the ETag -- not the date -- is the change signal.
CREATE TABLE IF NOT EXISTS flatfile_ingests (
    source_window text        PRIMARY KEY,   -- '1995-1999' ... '2025-2026'
    etag          text        NOT NULL,      -- S3 ETag of the ingested zip
    last_modified timestamptz,
    row_count     integer     NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now()
);
