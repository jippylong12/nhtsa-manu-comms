-- 0023_llm_batches.sql
-- Every Batch API job the pipeline submits, until its results are stored.
--
-- A batch is paid for when Anthropic finishes it, whether or not anyone collects the
-- results. The nightly run waits an hour for its batch; one that took longer was left
-- running and forgotten, so the next night sent the same documents again and paid for
-- them twice, and the first batch's cost never reached the usage ledger or the daily cap.
-- With this table the pipeline collects what is still out before it sends anything new,
-- and sends nothing new while a batch is still running.
CREATE TABLE IF NOT EXISTS llm_batches (
    batch_id     text        PRIMARY KEY,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    collected_at timestamptz             -- null while the results are not stored yet
);

CREATE INDEX IF NOT EXISTS llm_batches_open_idx ON llm_batches (submitted_at)
    WHERE collected_at IS NULL;
