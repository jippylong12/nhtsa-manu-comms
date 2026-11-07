-- 0012_pipeline_runs_reextract.sql
-- JIP-1579: `process --reextract` records two pipeline_runs rows, 'reextract-reset'
-- (the reset counts) and 'reextract' (the run, with ledger totals). The job CHECK from
-- 0002 lists only the original jobs, so it is dropped and re-added with those two
-- names. DROP IF EXISTS plus re-add makes a second run a no-op in effect.

ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_job_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_job_check
    CHECK (job IN ('sync', 'extract', 'llm', 'embed', 'digest', 'reextract', 'reextract-reset'));
