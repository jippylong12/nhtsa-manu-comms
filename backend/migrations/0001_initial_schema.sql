-- 0001_initial_schema.sql
-- Canonical Phase A schema: one communication row per nhtsa_id, linked to
-- vehicles through a join table. Replaces the Mongo design, which duplicated a
-- communication once per (nhtsa_id, vehicle_id) pair.

CREATE EXTENSION IF NOT EXISTS vector;

-- to_tsvector() is IMMUTABLE, but array_to_string() and concat_ws() are only
-- STABLE, so neither may appear in a GENERATED column expression. Wrapping
-- array_to_string in an IMMUTABLE SQL function is safe for text[] (the output
-- is fully deterministic) and lets the search vectors below stay generated
-- columns rather than trigger-maintained ones, which cannot drift.
CREATE OR REPLACE FUNCTION immutable_array_to_string(arr text[], sep text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT array_to_string(arr, sep) $$;

-- Keeps updated_at honest without the application having to remember.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


-- ---------------------------------------------------------------------------
-- vehicles: mirrors the tracked-vehicle config
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicles (
    id                bigserial PRIMARY KEY,
    nhtsa_vehicle_id  integer     NOT NULL UNIQUE,
    year              integer     NOT NULL,
    make              text        NOT NULL,
    model             text        NOT NULL,
    trim              text,
    keywords          text[]      NOT NULL DEFAULT '{}',
    active            boolean     NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS vehicles_active_idx ON vehicles (active) WHERE active;


-- ---------------------------------------------------------------------------
-- communications: canonical, one row per nhtsa_id
-- `status` is the only contract between the sync job (Job 1) and the
-- processor job (Job 2).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communications (
    id                   bigserial PRIMARY KEY,
    nhtsa_id             text        NOT NULL UNIQUE,
    communication_number text,
    communication_type   text,
    communication_date   timestamptz,
    summary              text,
    details_summary      text,
    raw                  jsonb       NOT NULL DEFAULT '{}'::jsonb,

    status               text        NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'processed', 'failed')),
    status_reason        text,
    attempts             integer     NOT NULL DEFAULT 0,
    processed_at         timestamptz,

    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(summary, '') || ' ' ||
            coalesce(details_summary, '') || ' ' ||
            coalesce(communication_number, '')
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS communications_status_idx ON communications (status);
CREATE INDEX IF NOT EXISTS communications_date_idx   ON communications (communication_date DESC);
CREATE INDEX IF NOT EXISTS communications_type_idx   ON communications (communication_type);
CREATE INDEX IF NOT EXISTS communications_tsv_idx    ON communications USING gin (search_tsv);
-- Partial index: the processor's hot query is "give me the next pending batch".
CREATE INDEX IF NOT EXISTS communications_pending_idx
    ON communications (created_at) WHERE status = 'pending';

DROP TRIGGER IF EXISTS communications_updated_at ON communications;
CREATE TRIGGER communications_updated_at BEFORE UPDATE ON communications
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------------
-- comm_documents: one row per associated PDF, holding extraction + LLM output
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comm_documents (
    id               bigserial PRIMARY KEY,
    communication_id bigint      NOT NULL REFERENCES communications (id) ON DELETE CASCADE,
    url              text        NOT NULL UNIQUE,
    doc_summary      text,
    load_date        timestamptz,

    -- Local extraction (PyMuPDF), plus the quality metrics the low-yield
    -- heuristic keys on.
    extracted_text    text,
    extraction_method text CHECK (extraction_method IN ('pymupdf', 'vision-fallback')),
    page_count        integer,
    char_count        integer,
    chars_per_page    numeric(10, 2),
    alpha_ratio       numeric(5, 3),
    extracted_at      timestamptz,

    -- LLM structured extraction
    llm_summary   text,
    -- Coarse document intent, from the A1 spike. Separates "this describes a
    -- fault" from "this is a warranty billing procedure", which the NHTSA
    -- communication_type does not reliably distinguish.
    doc_kind      text CHECK (doc_kind IN (
                      'service_procedure', 'warranty_admin', 'recall',
                      'customer_letter', 'informational', 'other')),
    symptoms      text[] NOT NULL DEFAULT '{}',
    systems       text[] NOT NULL DEFAULT '{}',
    components    text[] NOT NULL DEFAULT '{}',
    remedy        text,
    applicability text,
    llm_model     text,
    llm_tokens_in  integer,
    llm_tokens_out integer,
    llm_cost_usd   numeric(12, 8),
    llm_at         timestamptz,

    -- Embedding. 1536 dims, not gemini-embedding-001's native 3072: pgvector
    -- refuses to build an hnsw/ivfflat index above 2000 dims, and the model
    -- supports Matryoshka truncation to 1536 without retraining.
    embedding       vector(1536),
    embedding_model text,
    embedded_at     timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(llm_summary, '') || ' ' ||
            coalesce(remedy, '') || ' ' ||
            coalesce(applicability, '') || ' ' ||
            coalesce(immutable_array_to_string(symptoms, ' '), '') || ' ' ||
            coalesce(immutable_array_to_string(systems, ' '), '') || ' ' ||
            coalesce(immutable_array_to_string(components, ' '), '') || ' ' ||
            coalesce(extracted_text, '')
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS comm_documents_comm_idx ON comm_documents (communication_id);
CREATE INDEX IF NOT EXISTS comm_documents_tsv_idx  ON comm_documents USING gin (search_tsv);
-- Array GIN indexes back tag filtering (`symptoms @> ARRAY[...]`), which is a
-- different access path from the full-text search above.
CREATE INDEX IF NOT EXISTS comm_documents_symptoms_idx   ON comm_documents USING gin (symptoms);
CREATE INDEX IF NOT EXISTS comm_documents_systems_idx    ON comm_documents USING gin (systems);
CREATE INDEX IF NOT EXISTS comm_documents_components_idx ON comm_documents USING gin (components);
CREATE INDEX IF NOT EXISTS comm_documents_embedding_idx
    ON comm_documents USING hnsw (embedding vector_cosine_ops);

DROP TRIGGER IF EXISTS comm_documents_updated_at ON comm_documents;
CREATE TRIGGER comm_documents_updated_at BEFORE UPDATE ON comm_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------------
-- communication_vehicles: the join that removes the Mongo duplication
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_vehicles (
    communication_id bigint      NOT NULL REFERENCES communications (id) ON DELETE CASCADE,
    vehicle_id       bigint      NOT NULL REFERENCES vehicles (id)       ON DELETE CASCADE,
    matched_keywords text[]      NOT NULL DEFAULT '{}',
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (communication_id, vehicle_id)
);

CREATE INDEX IF NOT EXISTS communication_vehicles_vehicle_idx
    ON communication_vehicles (vehicle_id);
