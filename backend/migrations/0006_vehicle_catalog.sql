-- 0006_vehicle_catalog.sql
-- The YMM to vehicleId cache, off Mongo (JIP-420).
--
-- A hand-rolled cache in a document store that only ever wanted to be an
-- ordinary indexed table. It sits in front of NHTSA's byYmmt endpoint so the
-- add-vehicle flow does not pay a round trip per model selection: JIP-410
-- measured 560ms down to 3ms.
--
-- make and model are stored uppercased, which is what both writers already did,
-- so the lookup uppercases too and the plain btree index below serves it. A
-- functional index on lower(...) would work equally well but would be a second
-- convention for no gain.

CREATE TABLE IF NOT EXISTS vehicle_catalog (
    -- NHTSA's globally unique variant key, so it is the natural primary key and
    -- the upsert target. No surrogate id is useful here.
    vehicle_id integer     PRIMARY KEY,
    ncap_id    integer,
    year       integer     NOT NULL,
    make       text        NOT NULL,
    model      text        NOT NULL,
    trim       text,
    series     text,
    cached_at  timestamptz NOT NULL DEFAULT now()
);

-- The only lookup this table serves.
CREATE INDEX IF NOT EXISTS vehicle_catalog_ymm_idx
    ON vehicle_catalog (year, make, model);
