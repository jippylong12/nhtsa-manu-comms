-- 0002_pipeline_runs.sql
-- Per-run metrics for the sync and processor jobs. Phase A's closeout report
-- needs measured token counts and spend, not estimates, and the nightly runs
-- need somewhere to record that they happened and whether they succeeded.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           bigserial PRIMARY KEY,
    job          text        NOT NULL CHECK (job IN ('sync', 'extract', 'llm', 'embed', 'digest')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    ok           boolean,
    -- Free-form per-job counters (found/new/extracted/failed/...). Kept as
    -- jsonb so a new job can record new counters without a migration.
    counts       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    tokens_in    bigint      NOT NULL DEFAULT 0,
    tokens_out   bigint      NOT NULL DEFAULT 0,
    cost_usd     numeric(12, 8) NOT NULL DEFAULT 0,
    error        text
);

CREATE INDEX IF NOT EXISTS pipeline_runs_job_started_idx
    ON pipeline_runs (job, started_at DESC);

-- Watermark for the email digest, so a digest run knows what it already sent.
-- Single-row table by design: Phase A is single-tenant, and Phase C replaces
-- this with per-user, per-channel delivery records.
CREATE TABLE IF NOT EXISTS digest_state (
    id             boolean     PRIMARY KEY DEFAULT true CHECK (id),
    last_sent_at   timestamptz,
    last_watermark timestamptz
);

INSERT INTO digest_state (id) VALUES (true) ON CONFLICT (id) DO NOTHING;
