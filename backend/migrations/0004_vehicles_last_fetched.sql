-- 0004_vehicles_last_fetched.sql
-- Postgres becomes the only home for tracked vehicles (JIP-416).
--
-- Two columns the Mongo vehicle document carried and this table did not:
--
--   last_fetched  kept as a real column rather than derived from the newest
--                 linked communication. "When did we last ask NHTSA about this
--                 vehicle" and "when did a new bulletin last arrive" are
--                 different questions, and a fetch that finds nothing new still
--                 answers the first one. Deriving it would make a vehicle whose
--                 corpus is complete look permanently un-fetched.
--
--   comm_count    NOT a column. It is a count of communication_vehicles rows,
--                 and storing it is what let the card and the detail page
--                 disagree in the first place.

ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS last_fetched_at timestamptz;

-- vehicles was the one table created without this trigger, so updated_at only
-- moved when the application remembered to set it. It never did.
DROP TRIGGER IF EXISTS vehicles_updated_at ON vehicles;
CREATE TRIGGER vehicles_updated_at BEFORE UPDATE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Seed the new column for vehicles that already have a corpus. The newest link
-- is a lower bound on the last fetch, which beats showing "Never" for a vehicle
-- carrying hundreds of analysed bulletins. Vehicles whose Mongo document holds
-- a truer timestamp are corrected by migrate_vehicles_to_postgres.py.
UPDATE vehicles v
SET last_fetched_at = newest.seen
FROM (
    SELECT vehicle_id, max(first_seen_at) AS seen
    FROM communication_vehicles
    GROUP BY vehicle_id
) AS newest
WHERE newest.vehicle_id = v.id
  AND v.last_fetched_at IS NULL;
