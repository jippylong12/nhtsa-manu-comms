-- 0003_usage_events.sql
-- Canonical cost ledger: one row per billable model call.
--
-- Relationship to pipeline_runs: the two are kept separate on purpose.
-- `pipeline_runs` stays the OPERATIONAL record of a job execution (did the
-- nightly run happen, did it succeed, what did it count, what error). This
-- table is the BILLING record: one row per billable call, at a granularity
-- pipeline_runs cannot express now that query-time spend exists outside of any
-- job. Historical token/cost totals already recorded on pipeline_runs are
-- backfilled below so no spend history is lost.

CREATE TABLE IF NOT EXISTS usage_events (
    id           bigserial PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),

    -- What was paid for. Pipeline kinds and query-time kinds share one table so
    -- "what has this cost me" is a single scan.
    kind         text NOT NULL CHECK (kind IN (
                     'extract', 'llm_extract', 'embed',
                     'search_embed', 'answer_compose', 'digest')),
    model        text,

    tokens_in    bigint NOT NULL DEFAULT 0,
    tokens_out   bigint NOT NULL DEFAULT 0,
    -- Wide scale: a single query embedding costs ~1.5e-7 USD, and totals must
    -- still sum without losing those fractions.
    cost_usd     numeric(16, 10) NOT NULL DEFAULT 0,
    -- Items covered by this event (documents embedded in a batch, etc.).
    unit_count   integer NOT NULL DEFAULT 1,

    -- Optional context. ON DELETE SET NULL: purging a communication must not
    -- erase the record that money was spent processing it.
    communication_id bigint REFERENCES communications (id) ON DELETE SET NULL,
    document_id      bigint REFERENCES comm_documents (id) ON DELETE SET NULL,
    -- Groups the several events belonging to one symptom search.
    query_id     text,
    -- Reserved for Phase C so per-account attribution needs no migration.
    user_id      text,

    meta         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS usage_events_occurred_idx ON usage_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS usage_events_kind_occurred_idx ON usage_events (kind, occurred_at DESC);
CREATE INDEX IF NOT EXISTS usage_events_query_idx ON usage_events (query_id) WHERE query_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS usage_events_user_idx ON usage_events (user_id) WHERE user_id IS NOT NULL;


-- Backfill historical spend from pipeline_runs. Only runs that actually cost
-- something are carried over; `sync` does no model work. Each backfilled row
-- keeps its source run id in meta so the two tables can be reconciled.
INSERT INTO usage_events (
    occurred_at, kind, model, tokens_in, tokens_out, cost_usd, unit_count, meta
)
SELECT
    coalesce(r.finished_at, r.started_at),
    CASE r.job
        WHEN 'llm'     THEN 'llm_extract'
        WHEN 'embed'   THEN 'embed'
        WHEN 'extract' THEN 'extract'
        WHEN 'digest'  THEN 'digest'
    END,
    NULL,
    r.tokens_in,
    r.tokens_out,
    r.cost_usd,
    1,
    jsonb_build_object(
        'backfilled_from_pipeline_run', r.id,
        'job', r.job,
        'counts', r.counts
    )
FROM pipeline_runs r
WHERE r.job IN ('llm', 'embed', 'extract', 'digest')
  AND (r.tokens_in > 0 OR r.tokens_out > 0 OR r.cost_usd > 0);
