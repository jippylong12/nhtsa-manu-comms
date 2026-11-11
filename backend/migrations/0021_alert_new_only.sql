-- 0021_alert_new_only.sql
-- An alert email lists bulletins that are new from NHTSA, not bulletins we read for the
-- first time. Until now "new" meant processed after the user's watermark, so finishing the
-- first read of a car mailed its whole history (52 old bulletins in one email).
--
-- 1. When a car's first full sync finished. Bulletins linked before this are the car's
--    history; bulletins linked after it are news.
ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS first_synced_at timestamptz;

-- Cars already scanned, with every scanned bulletin linked, have had their first sync. A
-- car with a scanned bulletin not linked yet (its fetch failed) is left for sync to mark,
-- so that bulletin arrives as history, not as news.
UPDATE vehicles v
SET first_synced_at = now()
WHERE v.first_synced_at IS NULL
  AND EXISTS (SELECT 1 FROM vehicle_scans s WHERE s.vehicle_id = v.id)
  AND EXISTS (SELECT 1 FROM communication_vehicles cv WHERE cv.vehicle_id = v.id)
  AND NOT EXISTS (
      SELECT 1 FROM vehicle_bulletins vb
      WHERE vb.vehicle_id = v.id
        AND NOT EXISTS (
            SELECT 1 FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            WHERE c.nhtsa_id = vb.nhtsa_id AND cv.vehicle_id = v.id));

-- 2. The flag. Sync sets it on a link it creates for a car whose first sync is behind it;
--    the alert pass mails it to the car's subscribers and clears it. Existing links are
--    history: false.
ALTER TABLE communication_vehicles
    ADD COLUMN IF NOT EXISTS alert_pending boolean NOT NULL DEFAULT false;

-- Only the few waiting rows are indexed, so the alert pass never reads the whole table.
CREATE INDEX IF NOT EXISTS communication_vehicles_alert_pending_idx
    ON communication_vehicles (vehicle_id) WHERE alert_pending;
