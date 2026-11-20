-- When an account's email address was confirmed by link (roadmap 3b). NULL
-- means unconfirmed: alerts go only to confirmed addresses. Accounts created
-- before this column start unconfirmed.

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
