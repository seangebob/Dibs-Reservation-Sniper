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

from typing import Protocol
from uuid import UUID

from backend.db.postgres import PoolLike
from backend.models.watch import Watch


__all__ = [
    "TrackingHistoryRecorder",
    "WatchHistoryRecorder",
    "WatchHistoryRepository",
]


class _HistoryReadinessTracker(Protocol):
    """The one method `TrackingHistoryRecorder` needs on a readiness tracker.

    Structural so `backend.services.readiness.ReadinessTracker` satisfies it
    without this module importing that one -- keeping the dependency arrow
    from services -> db, not the other way around.
    """

    def record_history_outcome(self, *, ok: bool) -> None: ...


class WatchHistoryRecorder(Protocol):
    """The subset of `WatchHistoryRepository` `WatchService` depends on.

    Structural, mirroring how `NotificationService` decouples `WatchService`
    from any concrete notifier: a test double, or a future readiness-tracking
    decorator around the real repository (Task 7), only needs to satisfy this
    one method to stand in for it.
    """

    async def record(
        self,
        watch: Watch,
        owner_client_id: str | None = None,
        user_id: UUID | None = None,
    ) -> None: ...


_UPSERT_SQL = """
INSERT INTO watch_history (
    watch_id, owner_client_id, status, created_at, updated_at, expires_at,
    watch_json, user_id
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (watch_id) DO UPDATE SET
    -- A later call with no owner (e.g. a poll outcome, which carries no
    -- client identity) must never erase an owner recorded at creation. The
    -- account owner is preserved the same way -- once a watch belongs to an
    -- account, a subsequent ownerless poll outcome cannot unassign it.
    owner_client_id = COALESCE(EXCLUDED.owner_client_id, watch_history.owner_client_id),
    user_id = COALESCE(EXCLUDED.user_id, watch_history.user_id),
    status = EXCLUDED.status,
    updated_at = EXCLUDED.updated_at,
    expires_at = EXCLUDED.expires_at,
    watch_json = EXCLUDED.watch_json
""".strip()

_SELECT_ONE_SQL = "SELECT watch_json FROM watch_history WHERE watch_id = $1"

_SELECT_ACCOUNT_OWNER_SQL = "SELECT user_id FROM watch_history WHERE watch_id = $1"

_SELECT_FOR_OWNER_SQL = """
SELECT watch_json FROM watch_history
WHERE owner_client_id = $1
ORDER BY updated_at DESC
LIMIT $2
""".strip()

_SELECT_FOR_USER_SQL = """
SELECT watch_json FROM watch_history
WHERE user_id = $1
ORDER BY updated_at DESC
LIMIT $2
""".strip()

_CLAIM_ANONYMOUS_SQL = """
UPDATE watch_history SET user_id = $2
WHERE owner_client_id = $1 AND user_id IS NULL
""".strip()

#: A dashboard listing has no legitimate use for an unbounded result set; this
#: is a safety ceiling, not a pagination feature.
_MAX_LIST_LIMIT = 1000


class WatchHistoryRepository:
    """Upserts and reads the `watch_history` projection."""

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def record(
        self,
        watch: Watch,
        owner_client_id: str | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """Upsert the current public state of ``watch``.

        Safe to call repeatedly for the same watch as it moves through its
        lifecycle -- each call replaces the projection with the watch's
        current state, since this table holds current state, not history.

        ``user_id`` marks account ownership when the creator was authenticated
        (Requirement 3.1); like ``owner_client_id`` it never touches the public
        `Watch` model and is preserved across later ownerless updates.
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
                user_id,
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

    async def list_for_user(self, user_id: UUID, *, limit: int = 100) -> list[Watch]:
        """Return the account's watches, most recently updated first.

        The authenticated analogue of :meth:`list_for_owner`: `/api/watches/mine`
        scopes by ``user_id`` when the caller has a session (Requirement 3.2)."""

        if not 1 <= limit <= _MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIST_LIMIT}")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_FOR_USER_SQL, user_id, limit)
        return [Watch.model_validate_json(row["watch_json"]) for row in rows]

    async def claim_anonymous(self, owner_client_id: str, user_id: UUID) -> None:
        """Assign every still-unclaimed watch created under ``owner_client_id``
        to ``user_id`` (Requirement 4.1).

        Idempotent and non-stealing: the ``user_id IS NULL`` guard means a watch
        already owned by an account is never re-assigned, so a second signup or
        login with the same client id claims nothing new and never takes a watch
        from another account (Requirement 4.4)."""

        async with self._pool.acquire() as conn:
            await conn.execute(_CLAIM_ANONYMOUS_SQL, owner_client_id, user_id)

    async def get_account_owner(self, watch_id: str) -> UUID | None:
        """Return the account that owns ``watch_id``, or None when the watch is
        anonymous-owned or absent from the projection.

        This is the sole enforcement read (Requirement 3.3): a non-None result
        means access must be denied to any other (or no) account, while None
        keeps the Milestone 1-4 by-id behavior for anonymous watches."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ACCOUNT_OWNER_SQL, watch_id)
        if not rows:
            return None
        return rows[0]["user_id"]


class TrackingHistoryRecorder:
    """Reports every underlying `record(...)` outcome to a readiness tracker.

    Passes the call through unchanged (re-raising any exception exactly as the
    real repository would) after recording whether it succeeded, so the
    projection's readiness on `/health` reflects the actual write path rather
    than any inferred component state. `WatchService`'s own `_record_history`
    catches the re-raised exception and never propagates it to the caller
    (Task 4 wiring), so this decorator does not change any user-visible
    outcome or timing -- only the readiness signal.
    """

    def __init__(
        self,
        inner: WatchHistoryRecorder,
        tracker: _HistoryReadinessTracker,
    ) -> None:
        self._inner = inner
        self._tracker = tracker

    async def record(
        self,
        watch: Watch,
        owner_client_id: str | None = None,
        user_id: UUID | None = None,
    ) -> None:
        try:
            await self._inner.record(watch, owner_client_id, user_id)
        except Exception:
            self._tracker.record_history_outcome(ok=False)
            raise
        self._tracker.record_history_outcome(ok=True)
