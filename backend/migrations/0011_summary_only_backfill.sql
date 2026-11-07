-- 0011_summary_only_backfill.sql
-- JIP-1577 (decision 14): a communication NHTSA published without a document is
-- processed on its NHTSA summary, not failed. This moves the rows the old extract
-- stage marked failed with 'no associated documents published by NHTSA' to
-- processed with the summary-only reason (7 today). One set-based UPDATE; the
-- WHERE clause only matches rows still carrying the old failure, so a second
-- run changes nothing. The marker text must match SUMMARY_ONLY_REASON in
-- src/jobs/sync.py and extract.mark_summary_only_communications.

UPDATE communications
SET status = 'processed',
    processed_at = coalesce(processed_at, now()),
    status_reason = 'summary only: NHTSA published no document'
WHERE status = 'failed'
  AND status_reason = 'no associated documents published by NHTSA';
