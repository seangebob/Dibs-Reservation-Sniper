-- Accounts and opaque bearer sessions (Milestone 5).
--
-- Applied once by the same startup MigrationRunner as 0001. Only hashes are
-- stored: `users.password_hash` is an argon2 encoded hash and
-- `sessions.token_hash` is the sha256 of a bearer token whose raw value is
-- returned to the client exactly once and never persisted (Requirement 6.3).
--
-- `watch_history.user_id` links the Milestone 4 durable projection to an
-- account once a watch is created while authenticated or claimed on
-- signup/login (Requirements 3.1 / 4.1). It is additive and nullable, so every
-- existing anonymous row and every Milestone 1-4 caller is untouched.

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

ALTER TABLE watch_history ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);

CREATE INDEX IF NOT EXISTS watch_history_user_updated_idx
    ON watch_history (user_id, updated_at DESC);
