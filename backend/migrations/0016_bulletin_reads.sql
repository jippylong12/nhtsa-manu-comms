-- 0016_bulletin_reads.sql
-- Read state per account (project "consumer UI", decisions 3 and 11).
--
-- An owner wants to know which bulletins they have not read yet, on every device.
-- Severity stays a property of the bulletin; only read state is per user. A row is
-- recorded against the nhtsa_id of the latest revision of a family, so a new
-- revision arrives unread. Deleting a user deletes their read state.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS bulletin_reads (
    user_id  bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    nhtsa_id text        NOT NULL,
    read_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, nhtsa_id)
);

CREATE INDEX IF NOT EXISTS bulletin_reads_nhtsa_idx ON bulletin_reads (nhtsa_id);
