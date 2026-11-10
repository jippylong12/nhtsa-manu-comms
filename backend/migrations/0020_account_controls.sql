-- 0020_account_controls.sql
-- Three small things the account page needs.
--
-- 1. Alerts per car. An alert email covers every car on the account; this lets a
--    subscriber leave one car out. On by default.
ALTER TABLE user_vehicles ADD COLUMN IF NOT EXISTS alerts_enabled boolean NOT NULL DEFAULT true;

-- 2. Removals of a car that was on a subscription. A plan covers N cars at a time, so
--    removing and adding is how a subscriber would read more cars than the plan covers.
--    A plan allows a few removals per calendar month (see PLAN_REMOVALS in
--    src/me/service.py); this table is what that count reads. Removing an unlocked car,
--    or any car on an account without a subscription, is not recorded.
CREATE TABLE IF NOT EXISTS vehicle_removals (
    id         bigserial   PRIMARY KEY,
    user_id    bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    vehicle_id bigint      NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    removed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vehicle_removals_user_idx ON vehicle_removals (user_id, removed_at);

-- 3. A subscription the customer has cancelled keeps working until the period ends.
--    Stripe says so with cancel_at_period_end; the account page shows "ends" not "renews".
ALTER TABLE entitlements
    ADD COLUMN IF NOT EXISTS cancel_at_period_end boolean NOT NULL DEFAULT false;
