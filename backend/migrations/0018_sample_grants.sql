-- 0018_sample_grants.sql
-- Which cars an account has been given a free sample of, ever.
--
-- A free account may sample two different cars. `user_vehicles` cannot answer "how many
-- has this account sampled": a removed car leaves no row, so removing and adding would
-- hand out a fresh sample (and cost a fresh read) every time. This table remembers.
--
-- A grant stops counting against the two once the account has an active unlock for that
-- car, so paying for a car frees its slot. A subscriber or an admin is not limited; their
-- adds are still recorded, so a subscriber who cancels keeps the cars they had.

CREATE TABLE IF NOT EXISTS sample_grants (
    user_id    bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    vehicle_id bigint      NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, vehicle_id)
);

-- Every car an account already has was a sample it was given.
INSERT INTO sample_grants (user_id, vehicle_id, granted_at)
SELECT user_id, vehicle_id, created_at FROM user_vehicles
ON CONFLICT DO NOTHING;
