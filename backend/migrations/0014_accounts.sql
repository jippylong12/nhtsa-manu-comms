-- 0014_accounts.sql
-- Accounts for the product: Firebase-authenticated users, their saved vehicles,
-- paid entitlements, Stripe webhook idempotency and per-user alert state.
-- `vehicles` is untouched.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id            bigserial   PRIMARY KEY,
    firebase_uid  text        NOT NULL UNIQUE,
    email         text        NOT NULL,
    display_name  text,
    is_admin      boolean     NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS users_updated_at ON users;
CREATE TRIGGER users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS user_vehicles (
    user_id     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    vehicle_id  bigint      NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, vehicle_id)
);
CREATE INDEX IF NOT EXISTS user_vehicles_vehicle_idx ON user_vehicles (vehicle_id);

-- vehicle_id is NULL for a subscription, which covers every vehicle on the account.
CREATE TABLE IF NOT EXISTS entitlements (
    id                          bigserial   PRIMARY KEY,
    user_id                     bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind                        text        NOT NULL CHECK (kind IN ('unlock', 'subscription')),
    vehicle_id                  bigint      REFERENCES vehicles (id) ON DELETE CASCADE,
    status                      text        NOT NULL CHECK (status IN ('active', 'canceled', 'past_due')),
    stripe_customer_id          text,
    stripe_checkout_session_id  text        UNIQUE,
    stripe_subscription_id      text        UNIQUE,
    current_period_end          timestamptz,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entitlements_user_status_idx ON entitlements (user_id, status);
CREATE INDEX IF NOT EXISTS entitlements_vehicle_idx
    ON entitlements (vehicle_id) WHERE vehicle_id IS NOT NULL;

DROP TRIGGER IF EXISTS entitlements_updated_at ON entitlements;
CREATE TRIGGER entitlements_updated_at BEFORE UPDATE ON entitlements
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Webhook idempotency: an event id seen once is never processed twice.
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     text        PRIMARY KEY,
    type         text,
    received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_state (
    user_id            bigint      PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    last_watermark     timestamptz,
    last_sent_at       timestamptz,
    unsubscribed_at    timestamptz,
    unsubscribe_token  text        NOT NULL UNIQUE DEFAULT encode(gen_random_bytes(24), 'hex')
);
