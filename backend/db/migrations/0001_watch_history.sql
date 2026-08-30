-- Durable, best-effort projection of watch state (Milestone 4).
--
-- Written by WatchHistoryRepository after WatchService already committed an
-- outcome through the Milestone 3 Redis/in-memory WatchRepository. This table
-- is never read or written by the fenced single-flight polling protocol; its
-- only job is to survive that protocol's terminal-retention cleanup and
-- process restarts (Requirement 3.1/3.3). One row per watch, upserted on
-- every observable outcome -- a durable "current state" projection, not an
-- event log.
--
-- watch_json holds the exact `Watch.model_dump_json()` payload, so the public
-- Watch shape never needs a second, hand-maintained SQL schema to stay in
-- sync with backend/models/watch.py. The dedicated columns exist only for
-- what the dashboard actually queries/orders by.

CREATE TABLE IF NOT EXISTS watch_history (
    watch_id        TEXT PRIMARY KEY,
    owner_client_id TEXT,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    watch_json      JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS watch_history_owner_updated_idx
    ON watch_history (owner_client_id, updated_at DESC);
