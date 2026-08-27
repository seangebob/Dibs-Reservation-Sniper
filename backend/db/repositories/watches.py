"""Watch persistence: an interface, an in-memory store, and a Redis store."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any, Protocol

from pydantic import ValidationError

from backend.db.repositories.watch_decisions import (
    BookingPermit,
    BookingPermitStatus,
    ClaimResult,
    ClaimStatus,
    CommitResult,
    CommitStatus,
    CreateResult,
    CreateStatus,
    ScheduleMarker,
    TransitionResult,
    TransitionStatus,
    WindowClaim,
)
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import RuntimePhase, WatchRuntime


_TERMINAL_EVENT_STATUSES = frozenset(
    {WatchStatus.FOUND, WatchStatus.BOOKED, WatchStatus.EXPIRED}
)


def terminal_event_id(watch_id: str, status: WatchStatus, revision: int) -> str:
    """Deterministic id for one terminal transition, stable across processes.

    SHA-1 hex so the Redis implementation can reproduce the identical value
    with `redis.sha1hex`, which is what makes a terminal event observable at
    most once regardless of which store issued it.
    """

    digest = hashlib.sha1(
        f"{watch_id}:{status.value}:{revision}".encode()
    )
    return digest.hexdigest()


#: Every key this module owns lives under one prefix so a shared Redis
#: instance (the Celery broker uses the same server) stays legible.
KEY_PREFIX = "dibs:watch"
INDEX_KEY = "dibs:watches"
ACTIVE_INDEX_KEY = "dibs:watches:active"


class WatchRepository(Protocol):
    """Storage boundary for watches, implemented in memory and on Redis."""

    async def save(self, watch: Watch) -> Watch:
        """Insert or replace one watch."""
        ...

    async def get(self, watch_id: str) -> Watch | None:
        """Return one watch, or None when it is unknown."""
        ...

    async def list_all(self) -> list[Watch]:
        """Return every stored watch, newest first."""
        ...

    async def list_active(self) -> list[Watch]:
        """Return only watches the queue should still poll."""
        ...

    async def delete(self, watch_id: str) -> bool:
        """Remove one watch, returning whether it existed."""
        ...


@dataclass(slots=True)
class _ClaimLease:
    """One process's expiring ownership of a cadence window."""

    window_id: str
    owner_id: str
    token: int
    expires_at: datetime


class InMemoryWatchRepository:
    """Process-local store used by tests and by `--no-redis` local runs.

    Beyond the legacy save/get/list/delete surface, it implements the atomic
    state-machine protocol: create-with-schedule, claim, booking permit,
    fenced commit, and conditional cancel/expire. Every atomic decision is one
    critical section under `self._lock`, and lease/expiry comparisons use the
    injected clock, so concurrency behavior is deterministic in tests.

    It is equivalent to the Redis implementation within one process; it makes
    no claim that its state survives process loss.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._watches: dict[str, Watch] = {}
        self._runtimes: dict[str, WatchRuntime] = {}
        self._fence: dict[str, int] = {}
        self._claims: dict[str, _ClaimLease] = {}
        self._markers: dict[str, ScheduleMarker] = {}
        self._terminal_delete_at: dict[str, datetime] = {}
        self._events: set[str] = set()
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- legacy document surface (still used until the service is migrated) --

    async def save(self, watch: Watch) -> Watch:
        async with self._lock:
            self._watches[watch.watch_id] = watch
            return watch

    async def get(self, watch_id: str) -> Watch | None:
        async with self._lock:
            return self._watches.get(watch_id)

    async def list_all(self) -> list[Watch]:
        async with self._lock:
            watches = list(self._watches.values())
        return sorted(watches, key=lambda watch: watch.created_at, reverse=True)

    async def list_active(self) -> list[Watch]:
        return [
            watch
            for watch in await self.list_all()
            if watch.status is WatchStatus.ACTIVE
        ]

    async def delete(self, watch_id: str) -> bool:
        async with self._lock:
            existed = self._watches.pop(watch_id, None) is not None
            self._runtimes.pop(watch_id, None)
            self._fence.pop(watch_id, None)
            self._claims.pop(watch_id, None)
            self._markers.pop(watch_id, None)
            self._terminal_delete_at.pop(watch_id, None)
            return existed

    # -- atomic state-machine surface ---------------------------------------

    async def get_runtime(self, watch_id: str) -> WatchRuntime | None:
        async with self._lock:
            return self._runtimes.get(watch_id)

    async def schedule_marker(self, watch_id: str) -> ScheduleMarker | None:
        async with self._lock:
            return self._markers.get(watch_id)

    async def create_with_schedule(
        self,
        watch: Watch,
        runtime: WatchRuntime,
    ) -> CreateResult:
        async with self._lock:
            if watch.watch_id in self._watches:
                return CreateResult(
                    status=CreateStatus.ALREADY_EXISTS,
                    watch=self._watches[watch.watch_id],
                    runtime=self._runtimes.get(watch.watch_id),
                )
            self._watches[watch.watch_id] = watch
            self._runtimes[watch.watch_id] = runtime
            self._fence[watch.watch_id] = 0
            self._set_marker(watch.watch_id, runtime)
            return CreateResult(
                status=CreateStatus.CREATED, watch=watch, runtime=runtime
            )

    async def claim_window(
        self,
        watch_id: str,
        window_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> ClaimResult:
        async with self._lock:
            now = self._clock()
            watch = self._watches.get(watch_id)
            runtime = self._runtimes.get(watch_id)
            if watch is None or runtime is None:
                return ClaimResult(ClaimStatus.UNKNOWN)
            if watch.status.is_terminal:
                return ClaimResult(ClaimStatus.TERMINAL)
            if runtime.window_id != window_id:
                return ClaimResult(ClaimStatus.STALE)
            if runtime.scheduled_for is not None and runtime.scheduled_for > now:
                return ClaimResult(ClaimStatus.EARLY)

            held = self._claims.get(watch_id)
            if (
                held is not None
                and held.window_id == window_id
                and held.expires_at > now
            ):
                return ClaimResult(ClaimStatus.BUSY)

            token = self._fence.get(watch_id, 0) + 1
            self._fence[watch_id] = token
            lease_expires = now + timedelta(seconds=lease_seconds)
            self._claims[watch_id] = _ClaimLease(
                window_id=window_id,
                owner_id=owner_id,
                token=token,
                expires_at=lease_expires,
            )
            claimed_runtime = runtime.model_copy(
                update={"phase": RuntimePhase.POLLING}
            )
            self._runtimes[watch_id] = claimed_runtime
            return ClaimResult(
                status=ClaimStatus.OWNED,
                claim=WindowClaim(
                    watch=watch,
                    runtime=claimed_runtime,
                    owner_id=owner_id,
                    window_id=window_id,
                    token=token,
                    lease_expires_at=lease_expires,
                ),
            )

    async def begin_booking(self, claim: WindowClaim) -> BookingPermit:
        async with self._lock:
            watch_id = claim.watch.watch_id
            watch = self._watches.get(watch_id)
            runtime = self._runtimes.get(watch_id)
            if watch is None or runtime is None or watch.status.is_terminal:
                return BookingPermit(BookingPermitStatus.FENCED)
            if not self._owns(claim, runtime):
                return BookingPermit(BookingPermitStatus.FENCED)
            if runtime.cancel_requested:
                return BookingPermit(BookingPermitStatus.CANCELLED)
            self._runtimes[watch_id] = runtime.model_copy(
                update={"phase": RuntimePhase.BOOKING}
            )
            return BookingPermit(
                status=BookingPermitStatus.GRANTED,
                permit_id=f"{watch_id}:{claim.token}",
            )

    async def commit_window(
        self,
        claim: WindowClaim,
        new_watch: Watch,
        new_runtime: WatchRuntime,
    ) -> CommitResult:
        async with self._lock:
            watch_id = claim.watch.watch_id
            watch = self._watches.get(watch_id)
            runtime = self._runtimes.get(watch_id)
            if watch is None or runtime is None:
                return CommitResult(CommitStatus.UNKNOWN)
            if watch.status.is_terminal:
                return CommitResult(CommitStatus.TERMINAL, watch=watch)
            if not self._owns(claim, runtime):
                return CommitResult(CommitStatus.FENCED)

            revision = runtime.revision + 1
            stored_runtime = new_runtime.model_copy(
                update={"revision": revision, "phase": None}
            )
            self._watches[watch_id] = new_watch
            self._runtimes[watch_id] = stored_runtime
            self._claims.pop(watch_id, None)

            event_id: str | None = None
            if new_watch.status.is_terminal:
                self._markers.pop(watch_id, None)
                if stored_runtime.terminal_delete_at is not None:
                    self._terminal_delete_at[watch_id] = (
                        stored_runtime.terminal_delete_at
                    )
                event_id = self._issue_event(
                    watch_id, new_watch.status, revision
                )
            else:
                self._set_marker(watch_id, stored_runtime)
            return CommitResult(
                status=CommitStatus.COMMITTED,
                watch=new_watch,
                event_id=event_id,
            )

    async def cancel_if_active(self, watch_id: str) -> TransitionResult:
        async with self._lock:
            now = self._clock()
            watch = self._watches.get(watch_id)
            if watch is None:
                return TransitionResult(TransitionStatus.UNKNOWN)
            if watch.status.is_terminal:
                return TransitionResult(TransitionStatus.NOOP, watch=watch)
            runtime = self._runtimes.get(watch_id)
            if runtime is not None and runtime.phase is RuntimePhase.BOOKING:
                # A booking is in flight; record intent and let the owner
                # resolve it rather than falsely reporting a cancellation.
                self._runtimes[watch_id] = runtime.model_copy(
                    update={"cancel_requested": True}
                )
                return TransitionResult(
                    TransitionStatus.NOT_ELIGIBLE, watch=watch
                )
            cancelled = watch.model_copy(
                update={
                    "status": WatchStatus.CANCELLED,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
            self._apply_terminal(watch_id, cancelled, runtime)
            return TransitionResult(TransitionStatus.APPLIED, watch=cancelled)

    async def expire_if_eligible(
        self,
        watch_id: str,
        *,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> TransitionResult:
        async with self._lock:
            now = self._clock()
            watch = self._watches.get(watch_id)
            if watch is None:
                return TransitionResult(TransitionStatus.UNKNOWN)
            if watch.status.is_terminal:
                return TransitionResult(TransitionStatus.NOOP, watch=watch)
            runtime = self._runtimes.get(watch_id)
            if (
                expected_revision is not None
                and runtime is not None
                and runtime.revision != expected_revision
            ):
                return TransitionResult(TransitionStatus.FENCED)
            if not force and not watch.is_exhausted(now):
                return TransitionResult(
                    TransitionStatus.NOT_ELIGIBLE, watch=watch
                )
            expired = watch.model_copy(
                update={
                    "status": WatchStatus.EXPIRED,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
            revision = self._apply_terminal(watch_id, expired, runtime)
            event_id = self._issue_event(
                watch_id, WatchStatus.EXPIRED, revision
            )
            return TransitionResult(
                TransitionStatus.APPLIED, watch=expired, event_id=event_id
            )

    async def release_claim(self, claim: WindowClaim) -> bool:
        async with self._lock:
            held = self._claims.get(claim.watch.watch_id)
            if (
                held is not None
                and held.token == claim.token
                and held.owner_id == claim.owner_id
            ):
                del self._claims[claim.watch.watch_id]
                return True
            return False

    # -- internals ----------------------------------------------------------

    def _owns(self, claim: WindowClaim, current: WatchRuntime) -> bool:
        """Whether `claim` still holds an unexpired, un-fenced lease."""

        if current.revision != claim.runtime.revision:
            return False
        held = self._claims.get(claim.watch.watch_id)
        return (
            held is not None
            and held.token == claim.token
            and held.owner_id == claim.owner_id
            and held.expires_at > self._clock()
        )

    def _apply_terminal(
        self,
        watch_id: str,
        terminal_watch: Watch,
        runtime: WatchRuntime | None,
    ) -> int:
        """Store a terminal watch, bump revision, and clear scheduling state."""

        revision = (runtime.revision + 1) if runtime is not None else 0
        self._watches[watch_id] = terminal_watch
        if runtime is not None:
            self._runtimes[watch_id] = runtime.model_copy(
                update={
                    "revision": revision,
                    "window_id": None,
                    "scheduled_for": None,
                    "phase": None,
                    "cancel_requested": False,
                }
            )
        self._claims.pop(watch_id, None)
        self._markers.pop(watch_id, None)
        return revision

    def _set_marker(self, watch_id: str, runtime: WatchRuntime) -> None:
        if runtime.window_id is not None and runtime.scheduled_for is not None:
            self._markers[watch_id] = ScheduleMarker(
                watch_id=watch_id,
                window_id=runtime.window_id,
                scheduled_for=runtime.scheduled_for,
            )
        else:
            self._markers.pop(watch_id, None)

    def _issue_event(
        self,
        watch_id: str,
        status: WatchStatus,
        revision: int,
    ) -> str | None:
        if status not in _TERMINAL_EVENT_STATUSES:
            return None
        event_id = terminal_event_id(watch_id, status, revision)
        self._events.add(event_id)
        return event_id


class RedisWatchRepository:
    """Redis-backed store keeping one JSON document per watch.

    The client is injected rather than constructed here so the same repository
    works against a real server, a test double, or a future connection pool.
    Two sets index the documents: every watch, and the active subset the
    scheduler sweeps on startup.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def save(self, watch: Watch) -> Watch:
        pipeline = self._client.pipeline()
        pipeline.set(self._key(watch.watch_id), watch.model_dump_json())
        pipeline.sadd(INDEX_KEY, watch.watch_id)
        if watch.status is WatchStatus.ACTIVE:
            pipeline.sadd(ACTIVE_INDEX_KEY, watch.watch_id)
        else:
            pipeline.srem(ACTIVE_INDEX_KEY, watch.watch_id)
        await pipeline.execute()
        return watch

    async def get(self, watch_id: str) -> Watch | None:
        raw = await self._client.get(self._key(watch_id))
        return self._decode(watch_id, raw)

    async def list_all(self) -> list[Watch]:
        watch_ids = await self._client.smembers(INDEX_KEY)
        return await self._load_many(watch_ids)

    async def list_active(self) -> list[Watch]:
        watch_ids = await self._client.smembers(ACTIVE_INDEX_KEY)
        watches = await self._load_many(watch_ids)
        # The index can lag behind a document that was updated elsewhere, so
        # status on the document itself is the authority.
        return [watch for watch in watches if watch.status is WatchStatus.ACTIVE]

    async def delete(self, watch_id: str) -> bool:
        pipeline = self._client.pipeline()
        pipeline.delete(self._key(watch_id))
        pipeline.srem(INDEX_KEY, watch_id)
        pipeline.srem(ACTIVE_INDEX_KEY, watch_id)
        results = await pipeline.execute()
        return bool(results[0])

    async def _load_many(self, watch_ids: Any) -> list[Watch]:
        ordered_ids = sorted(watch_ids or ())
        if not ordered_ids:
            return []

        raw_documents = await self._client.mget(
            [self._key(watch_id) for watch_id in ordered_ids]
        )
        watches = [
            watch
            for watch_id, raw in zip(ordered_ids, raw_documents, strict=True)
            if (watch := self._decode(watch_id, raw)) is not None
        ]
        return sorted(watches, key=lambda watch: watch.created_at, reverse=True)

    @staticmethod
    def _decode(watch_id: str, raw: Any) -> Watch | None:
        """Parse a stored document, treating corrupt JSON as a missing watch.

        A document written by an older schema must not take down the whole
        listing, and there is nothing useful a caller could do with it.
        """

        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            watch = Watch.model_validate_json(raw)
        except ValidationError:
            return None
        # The key/index identity is authoritative. Returning a valid document
        # for another watch would make reads and listings silently lie.
        if watch.watch_id != watch_id:
            return None
        return watch

    @staticmethod
    def _key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}"
