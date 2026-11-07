-- 0008_plain_nameplate_identity.sql
-- Vehicle identity becomes (year, make, model), ADR 0001 section 1.
--
-- After the plain-nameplate re-key there is no NHTSA vehicleId to key on:
-- vehicles.id is the one identity the API, jobs and refresh use, and the domain
-- identity is (year, make, model) with model stored in canonical plain-nameplate
-- form. This migration is additive/relaxing only, so it is safe to run before
-- the data backfill: nhtsa_vehicle_id stays as nullable legacy data that nothing
-- reads. The destructive drops (trim, nhtsa_vehicle_id, vehicle_catalog) and the
-- backfill are 0009 (JIP-433).

ALTER TABLE vehicles ALTER COLUMN nhtsa_vehicle_id DROP NOT NULL;
ALTER TABLE vehicles DROP CONSTRAINT IF EXISTS vehicles_nhtsa_vehicle_id_key;

-- Partial: a soft-deleted vehicle (active = false) may share an identity with a
-- live one. upsert_vehicle revives the inactive row rather than relying on ON
-- CONFLICT, which a partial index cannot target.
CREATE UNIQUE INDEX IF NOT EXISTS vehicles_active_identity_uidx
    ON vehicles (year, make, model) WHERE active;
