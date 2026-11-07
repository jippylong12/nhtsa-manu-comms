-- 0009_drop_trim_and_vehicle_catalog.sql
-- The destructive schema cleanup the plain-nameplate ADR owes (ADR 0001, JIP-433).
--
-- After the re-key (0008, JIP-1561) a vehicle is identified by (year, make, model)
-- with model in canonical plain-nameplate form. Nothing reads vehicles.trim any
-- more -- the crash-test body-style detail it carried ('2 DR', 'PU/RC', '4 DR')
-- is exactly the taxonomy comms are not filed under -- and vehicle_catalog, the
-- YMM to NHTSA vehicleId cache from 0006, has no reader after the flat-file index
-- (comm_products, 0007) replaced it.
--
-- Run the backfill (python -m src.scripts.backfill_plain_nameplate --apply)
-- before deploying code that depends on canonical models; this migration is
-- independent of it and safe in either order. Not reversible: the replica's trim
-- values at the time were NULL, NULL, NULL, '2 DR', 'PU/RC', '4 DR'.

ALTER TABLE vehicles DROP COLUMN IF EXISTS trim;
DROP TABLE IF EXISTS vehicle_catalog;
