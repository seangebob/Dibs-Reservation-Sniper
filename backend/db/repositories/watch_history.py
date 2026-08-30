"""Durable, best-effort PostgreSQL projection of watch state (Milestone 4).

This repository is a passive observer of `WatchService`'s outcomes, never a
participant in the fenced single-flight polling protocol Milestone 3 built.
It exists solely so a watch's last known public state survives Milestone 3's
terminal-retention cleanup and process restarts (design.md's "Key decision").

Every method here can raise on a Postgres failure -- deliberately, so this
class stays a thin, honestly-typed data-access layer. Swallowing failures so
they never affect a live watch operation is the wiring layer's job (Task 4),
not this repository's.
"""

from __future__ import annotations

from backend.db.postgres import PoolLike
from backend.models.watch import Watch


__all__ = ["WatchHistoryRepository"]


_UPSERT_SQL = """
INSERT INTO watch_history (
    watch_id, owner_client_id, status, created_at, updated_at, expires_at, watch_json
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (watch_id) DO UPDATE SET
    -- A later call with no owner (e.g. a poll outcome, which carries no
    -- client identity) must never erase an owner recorded at creation.
    owner_client_id = COALESCE(EXCLUDED.owner_client_id, watch_history.owner_client_id),
    status = EXCLUDED.status,
    updated_at = EXCLUDED.updated_at,
    expires_at = EXCLUDED.expires_at,
    watch_json = EXCLUDED.watch_json
""".strip()

_SELECT_ONE_SQL = "SELECT watch_json FROM watch_history WHERE watch_id = $1"

_SELECT_FOR_OWNER_SQL = """
SELECT watch_json FROM watch_history
WHERE owner_client_id = $1
ORDER BY updated_at DESC
LIMIT $2
""".strip()

#: A dashboard listing has no legitimate use for an unbounded result set; this
#: is a safety ceiling, not a pagination feature.
_MAX_LIST_LIMIT = 1000


class WatchHistoryRepository:
    """Upserts and reads the `watch_history` projection."""

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def record(self, watch: Watch, owner_client_id: str | None = None) -> None:
        """Upsert the current public state of ``watch``.

        Safe to call repeatedly for the same watch as it moves through its
        lifecycle -- each call replaces the projection with the watch's
        current state, since this table holds current state, not history.
        """

        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                watch.watch_id,
                owner_client_id,
                watch.status.value,
                watch.created_at,
                watch.updated_at,
                watch.expires_at,
                watch.model_dump_json(),
            )

    async def get(self, watch_id: str) -> Watch | None:
        """Return the durable projection for ``watch_id``, if one exists.

        Returns the same result whether or not the live Milestone 3 store
        still tracks this watch -- this is exactly the point of the
        projection (Requirement 3.3).
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ONE_SQL, watch_id)
        if not rows:
            return None
        return Watch.model_validate_json(rows[0]["watch_json"])

    async def list_for_owner(
        self, owner_client_id: str, *, limit: int = 100
    ) -> list[Watch]:
        """Return ``owner_client_id``'s watches, most recently updated first."""

        if not 1 <= limit <= _MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIST_LIMIT}")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_FOR_OWNER_SQL, owner_client_id, limit)
        return [Watch.model_validate_json(row["watch_json"]) for row in rows]
