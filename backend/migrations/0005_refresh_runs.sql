-- 0005_refresh_runs.sql
-- State for an in-app refresh: sync then process, for one vehicle (JIP-418).
--
-- Why a table rather than in-memory state behind an SSE connection: a run takes
-- minutes, and the user is expected to navigate away and come back. Progress has
-- to outlive both the HTTP connection and, ideally, the worker holding it. The
-- UI polls this row.
--
-- Relationship to pipeline_runs: that table stays the record of a whole CLI
-- pipeline execution across every vehicle. This one is the record of a single
-- user-initiated refresh of a single vehicle, which is a different question with
-- a different lifetime.

CREATE TABLE IF NOT EXISTS refresh_runs (
    id          bigserial   PRIMARY KEY,
    vehicle_id  bigint      NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,

    status      text        NOT NULL
                            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    -- Named for the user, not the pipeline. 'analyse' covers the Gemini batch,
    -- which is the long quiet stretch the progress display exists to explain.
    stage       text        CHECK (stage IN ('sync', 'read', 'analyse', 'index', 'finalise')),
    stage_note  text,
    done        integer     NOT NULL DEFAULT 0,
    total       integer     NOT NULL DEFAULT 0,

    -- The preview the user confirmed, kept so the finished run can be compared
    -- against what it promised.
    expected_documents  integer         NOT NULL DEFAULT 0,
    estimated_cost_usd  numeric(12, 6)  NOT NULL DEFAULT 0,
    actual_cost_usd     numeric(16, 10),

    counts      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error       text,

    started_at   timestamptz NOT NULL DEFAULT now(),
    -- Bumped on every progress write. A worker killed mid-run (a reload, a
    -- laptop sleeping) leaves its row 'running' forever otherwise, and one
    -- abandoned row would block every future refresh.
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,

    -- Holds `true` while the run is active and NULL once it is not. Duplicate
    -- NULLs are allowed in a unique btree index and duplicate `true`s are not,
    -- so the index below admits exactly one active run at a time. A constant
    -- index expression is not accepted, hence a real column.
    active_lock boolean,
    CONSTRAINT refresh_runs_lock_matches_status
        CHECK ((status IN ('queued', 'running')) = (active_lock IS NOT NULL))
);

-- One refresh at a time across the whole app, not one per vehicle. The process
-- stages walk shared tables and a bulletin can belong to several vehicles, so
-- two overlapping runs could pay twice for the same document.
CREATE UNIQUE INDEX IF NOT EXISTS refresh_runs_one_active ON refresh_runs (active_lock);

CREATE INDEX IF NOT EXISTS refresh_runs_vehicle_idx
    ON refresh_runs (vehicle_id, started_at DESC);
