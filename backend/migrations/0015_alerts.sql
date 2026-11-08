-- 0015_alerts.sql
-- Per-user alerts replace the single-row digest watermark. The admin's existing
-- watermark is carried into alert_state (when an admin user and a digest_state row
-- both exist; otherwise alert_state stays empty for him and his first alert run
-- starts from the beginning), then digest_state goes. Idempotent: a re-run finds no
-- digest_state and does nothing; it never touches other users' alert_state rows.

DO $$
BEGIN
    IF to_regclass('public.digest_state') IS NOT NULL THEN
        INSERT INTO alert_state (user_id, last_watermark, last_sent_at)
        SELECT u.id, d.last_watermark, d.last_sent_at
        FROM digest_state d, users u
        WHERE u.is_admin
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

DROP TABLE IF EXISTS digest_state;
