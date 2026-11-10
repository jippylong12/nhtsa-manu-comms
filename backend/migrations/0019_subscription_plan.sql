-- 0019_subscription_plan.sql
-- Which subscription an account bought.
--
-- There are two: `monthly` ($5 a month, 2 cars) and `all_access` ($20 a month, 20 cars).
-- Both are `kind = 'subscription'`, so everything that asks "is this a subscriber"
-- (full access, alerts) is unchanged; only the car limit reads `plan`. An unlock has no plan.

ALTER TABLE entitlements ADD COLUMN IF NOT EXISTS plan text
    CHECK (plan IN ('monthly', 'all_access'));

-- Every subscription sold before this was the $5 one.
UPDATE entitlements SET plan = 'monthly' WHERE kind = 'subscription' AND plan IS NULL;
