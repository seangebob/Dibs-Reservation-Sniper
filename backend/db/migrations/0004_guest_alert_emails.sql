-- Email alerts for guests: no account, just a verified address (roadmap 3b).
--
-- A guest is the anonymous `X-Dibs-Client-Id` that already owns their watches
-- in `watch_history.owner_client_id`. They give an address, Dibs emails a
-- single-use link, and once it is opened the address in `guest_alert_emails`
-- receives that browser's watch alerts. Only a sha256 of the link's token is
-- stored, as with session tokens.

CREATE TABLE IF NOT EXISTS guest_email_verifications (
    token_hash  TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS guest_email_verifications_client_idx
    ON guest_email_verifications (client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS guest_alert_emails (
    client_id   TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL
);
