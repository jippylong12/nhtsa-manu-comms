-- 0024_search_quota.sql
-- A paying account gets 1,000 symptom searches per payment. Every payment (a car unlock, a
-- new subscription, a subscription renewal) starts the count again from zero; the count
-- does not carry over. An account that pays once and never again has 1,000 in total.
--
-- This column is the moment of the account's latest payment. The searches themselves are
-- already in usage_events (one `search_embed` row per search); the limit counts the rows
-- after this moment.
ALTER TABLE users ADD COLUMN IF NOT EXISTS searches_reset_at timestamptz;

-- Accounts that already paid start from their latest purchase.
UPDATE users u
SET searches_reset_at = e.latest
FROM (SELECT user_id, max(created_at) AS latest FROM entitlements GROUP BY user_id) e
WHERE e.user_id = u.id AND u.searches_reset_at IS NULL;
