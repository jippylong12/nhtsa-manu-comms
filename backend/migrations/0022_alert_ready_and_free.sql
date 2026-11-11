-- 0022_alert_ready_and_free.sql
-- Two kinds of email, one format ("we found X bulletins", a serious / medium / small
-- breakdown, a short preview):
--   ready: a car's first read finished; every account that was waiting on it is told once.
--   new:   the nightly found new bulletins for a car that was already read.
-- Free accounts now get both (the second as a teaser with an unlock button), so they need
-- a starting point too.
--
-- 1. When a car's first read finished: its first sync is done and nothing linked to it is
--    still waiting to be read. Set once by the alert pass. "Ready" used to mean "the first
--    bulletin was processed", which fired before the total was known.
ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS ready_at timestamptz;

-- Every car with a bulletin already read was ready, in the old sense, when its first one
-- was processed, and its accounts were told then. That holds whatever is still waiting on
-- it today (a backlog behind the spend cap, a retry, a late document): leaving such a car
-- unstamped would announce its whole history as "ready" on the next pass.
UPDATE vehicles v
SET ready_at = p.first_processed
FROM (
    SELECT cv.vehicle_id, min(c.processed_at) AS first_processed
    FROM communication_vehicles cv JOIN communications c ON c.id = cv.communication_id
    WHERE c.status = 'processed' AND c.processed_at IS NOT NULL
    GROUP BY cv.vehicle_id
) p
WHERE p.vehicle_id = v.id AND v.ready_at IS NULL;

-- 2. Every existing account starts from now, so the first pass after this migration does
--    not mail anyone about cars or bulletins from before it. An account that was already
--    mailed keeps its watermark; a row with none (never mailed, or made by unsubscribing)
--    starts from now too.
INSERT INTO alert_state (user_id, last_watermark)
SELECT u.id, now() FROM users u
ON CONFLICT (user_id) DO UPDATE SET last_watermark = now()
WHERE alert_state.last_watermark IS NULL;
