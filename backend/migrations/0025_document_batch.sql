-- 0025_document_batch.sql
-- Which Batch API job a document was last sent in.
--
-- 0023 tracks the jobs that are still out, but a document inside one still looked
-- unread to everything else: adding a car, an admin refresh or a --sync run read it
-- again at full price while the batch was still running, and the batch's own paid
-- result was then thrown away. With this column a document in a job that is still out
-- (llm_batches.collected_at IS NULL) is not picked up by any other read.
ALTER TABLE comm_documents ADD COLUMN IF NOT EXISTS llm_batch_id text;

CREATE INDEX IF NOT EXISTS comm_documents_llm_batch_idx
    ON comm_documents (llm_batch_id) WHERE llm_batch_id IS NOT NULL;
