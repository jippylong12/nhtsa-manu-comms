-- 0010_schema_v2.sql
-- Schema v2: structured applicability, audience, and severity.
--
-- comm_documents gains the applicability arrays (years, models), a free-text
-- conditions field and the audience (owner / dealer / both) the PDF extraction
-- will fill; the prose `applicability` column and `search_tsv` are untouched.
-- communications gains `severity` (serious / medium / small) and where it came
-- from (`severity_source`: the NHTSA type mapping, or the document classifier).
-- Existing rows are backfilled from the type mapping in one set-based UPDATE,
-- limited to severity_source = 'type' so a classifier result is never
-- overwritten. Everything is IF NOT EXISTS, so a second run is a no-op. The
-- mapping lives in src/communications/severity.py; keep the two in step.

ALTER TABLE comm_documents
    ADD COLUMN IF NOT EXISTS applicability_years integer[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS applicability_models text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS applicability_conditions text,
    ADD COLUMN IF NOT EXISTS audience text CHECK (audience IN ('owner','dealer','both'));

ALTER TABLE communications
    ADD COLUMN IF NOT EXISTS severity text NOT NULL DEFAULT 'small'
        CHECK (severity IN ('serious','medium','small')),
    ADD COLUMN IF NOT EXISTS severity_source text NOT NULL DEFAULT 'type'
        CHECK (severity_source IN ('type','classifier'));

UPDATE communications SET severity = CASE
    WHEN communication_type IN ('TSB','CSP','RC') THEN 'serious'
    WHEN communication_type IN ('PIT','PIC','SB','TB','SU','WA','SC') THEN 'medium'
    ELSE 'small' END
WHERE severity_source = 'type';

CREATE INDEX IF NOT EXISTS communications_severity_idx ON communications (severity);
CREATE INDEX IF NOT EXISTS comm_documents_applicability_years_idx
    ON comm_documents USING gin (applicability_years);
CREATE INDEX IF NOT EXISTS comm_documents_applicability_models_idx
    ON comm_documents USING gin (applicability_models);
