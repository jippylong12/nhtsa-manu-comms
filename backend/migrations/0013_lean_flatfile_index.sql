-- 0013_lean_flatfile_index.sql
-- Replace the 1.5 GB comm_products mirror of NHTSA's catalogue with two small
-- tables (project "lean flat-file index", decisions 1-6 and 10).
--
-- comm_products held one row per (communication, make, model, year): 3.9 million
-- rows to answer two questions -- "what can the picker offer" and "which
-- bulletins belong to a tracked vehicle". Neither needs the catalogue.
--
--   comm_vocab        the year/make/model vocabulary the picker reads, with a
--                     distinct-bulletin count per triple per window (the counts
--                     drive flatfile.resolve_models, which folds spelling
--                     variants and REDUNDANT/DUPLICATE copies). About 92k rows.
--   vehicle_bulletins the bulletins of each tracked vehicle, filled by scanning
--                     the flat files for tracked identities only, with alias
--                     folding applied at scan time. Sync, preview and the
--                     identity check read this instead of the catalogue.
--   vehicle_scans     which (vehicle, window) pairs have been scanned, and at
--                     which window etag. A pair is stale when its row is missing
--                     or its etag differs from flatfile_ingests.etag, and
--                     flatfile.refresh_index repairs stale pairs.
--
-- The migration derives comm_vocab and the exact-identity bulletins of the
-- active vehicles from comm_products, then drops it. vehicle_scans is left empty
-- on purpose: the first --bootstrap or refresh rescans every vehicle with alias
-- folding, which the exact-identity derivation below cannot do. The runner
-- records the file, so the derive-and-drop happens exactly once per database.

CREATE TABLE IF NOT EXISTS comm_vocab (
    model_year    int  NOT NULL,          -- 9999 = unknown, as published
    make          text NOT NULL,          -- uppercase, as published
    model         text NOT NULL,          -- flatfile.normalize_model form
    source_window text NOT NULL,          -- e.g. '2020-2024'; rebuilt per window on ingest
    bulletins     int  NOT NULL,          -- distinct nhtsa_ids for this triple in this window
    PRIMARY KEY (model_year, make, model, source_window)
);

CREATE TABLE IF NOT EXISTS vehicle_bulletins (
    vehicle_id    bigint NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    nhtsa_id      text   NOT NULL,        -- joins to communications.nhtsa_id
    summary       text   NOT NULL DEFAULT '',
    source_window text   NOT NULL,        -- the window whose scan last wrote the row
    PRIMARY KEY (vehicle_id, nhtsa_id)
);

CREATE TABLE IF NOT EXISTS vehicle_scans (
    vehicle_id    bigint NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    source_window text   NOT NULL,
    etag          text   NOT NULL,        -- the window etag the scan read
    scanned_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, source_window)
);

INSERT INTO comm_vocab (model_year, make, model, source_window, bulletins)
SELECT model_year, make, model, source_window, count(DISTINCT nhtsa_id)
FROM comm_products
GROUP BY 1, 2, 3, 4
ON CONFLICT DO NOTHING;

INSERT INTO vehicle_bulletins (vehicle_id, nhtsa_id, summary, source_window)
SELECT DISTINCT ON (v.id, p.nhtsa_id) v.id, p.nhtsa_id, coalesce(p.summary, ''), p.source_window
FROM vehicles v
JOIN comm_products p
  ON p.model_year = v.year AND p.make = v.make AND p.model = v.model
WHERE v.active
ORDER BY v.id, p.nhtsa_id, p.source_window DESC
ON CONFLICT DO NOTHING;

-- vehicle_scans stays empty on purpose: the first --bootstrap / refresh rescans
-- with alias folding.

DROP TABLE comm_products;
