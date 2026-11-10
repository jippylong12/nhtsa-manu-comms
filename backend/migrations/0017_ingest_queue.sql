-- 0017_ingest_queue.sql
-- Cars waiting to be read after a user added them.
--
-- Before this, adding a car started its read at once and, when another read was
-- already running, did nothing and left the car for the nightly run. Now the add
-- puts the car here and a background worker in the API reads the queue one car at
-- a time, then stops until the next add. The table is the queue's memory: a
-- restart or a deploy loses nothing, the worker picks up where the rows say.
--
-- One row per vehicle (several users adding the same car queue it once). A read
-- that fails is retried on the worker's next passes up to a small limit, kept in
-- `attempts`; the nightly run reads every tracked vehicle regardless, so a row
-- that gives up here is still read that night.

CREATE TABLE IF NOT EXISTS ingest_queue (
    vehicle_id   bigint      PRIMARY KEY REFERENCES vehicles (id) ON DELETE CASCADE,
    requested_at timestamptz NOT NULL DEFAULT now(),
    attempts     integer     NOT NULL DEFAULT 0,
    last_error   text
);

CREATE INDEX IF NOT EXISTS ingest_queue_requested_idx ON ingest_queue (requested_at);
