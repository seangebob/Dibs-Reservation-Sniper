-- Single-use emailed links for accounts (roadmap 3b): password reset now,
-- email confirmation next. Like sessions, only a sha256 of each link's token
-- is stored. Using a link sets `used_at` in the same statement that reads it,
-- so a link can never work twice.

CREATE TABLE IF NOT EXISTS account_links (
    token_hash  TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL CHECK (purpose IN ('password_reset', 'email_verify')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS account_links_user_purpose_idx
    ON account_links (user_id, purpose);
