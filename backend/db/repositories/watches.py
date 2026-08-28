"""Watch persistence: an interface, an in-memory store, and a Redis store."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any, Protocol

from pydantic import ValidationError

from backend.db.repositories import watch_scripts
from backend.db.repositories.watch_decisions import (
    BookingPermit,
    BookingPermitStatus,
    ClaimResult,
    ClaimStatus,
    CommitResult,
    CommitStatus,
    CreateResult,
    CreateStatus,
    DispatchClaim,
    DispatchResult,
    DispatchStatus,
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


def _text(value: Any) -> str:
    """Decode a Redis reply element to str, tolerating a non-decoding client."""

    return value.decode("utf-8") if isinstance(value, bytes) else value


def _code(reply: Any) -> str:
    """First element of a script reply: its decision code."""

    return _text(reply[0])


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _from_ms(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, UTC)


def dispatch_window_hash(window_id: str) -> str:
    """Short, bounded, deterministic key suffix for one window's dispatch lease.

    Hashing keeps the key length bounded regardless of the window id and gives
    the same suffix in both stores, so a lease acquired by one process is
    addressable by any other.
    """

    return hashlib.sha1(window_id.encode()).hexdigest()[:16]


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
SCHEDULE_INDEX_KEY = "dibs:watches:schedule"
TERMINAL_INDEX_KEY = "dibs:watches:terminal"
EVENTS_KEY = "dibs:watch:events"


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

    # Atomic state-machine surface (implemented by both stores).

    async def get_runtime(self, watch_id: str) -> WatchRuntime | None:
        """Return the internal sidecar, or None when unknown."""
        ...

    async def schedule_marker(self, watch_id: str) -> ScheduleMarker | None:
        """Return the current due-window marker, or None."""
        ...

    async def due_schedule_markers(
        self,
        now: datetime,
        horizon_seconds: float,
        limit: int,
    ) -> list[ScheduleMarker]:
        """Return markers whose due time is within the dispatch horizon."""
        ...

    async def claim_dispatch(
        self,
        marker: ScheduleMarker,
        owner_id: str,
        lease_seconds: float,
    ) -> DispatchResult:
        """Acquire the single-flight lease to publish one marker."""
        ...

    async def mark_dispatched(
        self,
        claim: DispatchClaim,
        redispatch_after: datetime,
    ) -> bool:
        """Record broker acceptance, deferring redispatch until the grace time."""
        ...

    async def release_dispatch(self, claim: DispatchClaim) -> bool:
        """Release a still-owned dispatch lease so the marker is redispatchable."""
        ...

    async def create_with_schedule(
        self,
        watch: Watch,
        runtime: WatchRuntime,
    ) -> CreateResult:
        """Persist a new watch, its sidecar, and its first schedule atomically."""
        ...

    async def claim_window(
        self,
        watch_id: str,
        window_id: str,
        owner_id: str,
        lease_seconds: float,
        *,
        ignore_schedule: bool = False,
    ) -> ClaimResult:
        """Grant one expiring, fenced claim on a due cadence window.

        `ignore_schedule` suppresses the not-yet-due `EARLY` guard for the
        legacy `poll_once` path, where the arrival of the job is itself the
        authority that the window is due; the window-aware path leaves it on.
        """
        ...

    async def begin_booking(self, claim: WindowClaim) -> BookingPermit:
        """Linearize the point before an irreversible booking call."""
        ...

    async def commit_window(
        self,
        claim: WindowClaim,
        new_watch: Watch,
        new_runtime: WatchRuntime,
    ) -> CommitResult:
        """Apply a claimed window's result iff the claim still owns it."""
        ...

    async def cancel_if_active(self, watch_id: str) -> TransitionResult:
        """Cancel an active watch, fencing any in-flight claim."""
        ...

    async def expire_if_eligible(
        self,
        watch_id: str,
        *,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> TransitionResult:
        """Expire an exhausted or overdue watch conditionally."""
        ...

    async def release_claim(self, claim: WindowClaim) -> bool:
        """Release a still-owned, uncommitted claim."""
        ...


@dataclass(slots=True)
class _ClaimLease:
    """One process's expiring ownership of a cadence window."""

    window_id: str
    owner_id: str
    token: int
    expires_at: datetime


@dataclass(slots=True)
class _DispatchLease:
    """One process's expiring right to publish a window to the queue."""

    window_id: str
    owner_id: str
    generation: int
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
        self._dispatch_fence: dict[str, int] = {}
        self._dispatch_leases: dict[str, _DispatchLease] = {}
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
            self._dispatch_fence.pop(watch_id, None)
            self._dispatch_leases.pop(watch_id, None)
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

    async def due_schedule_markers(
        self,
        now: datetime,
        horizon_seconds: float,
        limit: int,
    ) -> list[ScheduleMarker]:
        async with self._lock:
            cutoff = now + timedelta(seconds=horizon_seconds)
            due = [
                marker
                for marker in self._markers.values()
                if marker.scheduled_for <= cutoff
            ]
        due.sort(key=lambda marker: marker.scheduled_for)
        return due[:limit]

    async def claim_dispatch(
        self,
        marker: ScheduleMarker,
        owner_id: str,
        lease_seconds: float,
    ) -> DispatchResult:
        async with self._lock:
            now = self._clock()
            watch = self._watches.get(marker.watch_id)
            current = self._markers.get(marker.watch_id)
            if (
                watch is None
                or watch.status.is_terminal
                or current is None
                or current.window_id != marker.window_id
            ):
                return DispatchResult(DispatchStatus.STALE)

            held = self._dispatch_leases.get(marker.watch_id)
            if (
                held is not None
                and held.window_id == marker.window_id
                and held.expires_at > now
            ):
                return DispatchResult(DispatchStatus.BUSY)

            generation = self._dispatch_fence.get(marker.watch_id, 0) + 1
            self._dispatch_fence[marker.watch_id] = generation
            expires = now + timedelta(seconds=lease_seconds)
            self._dispatch_leases[marker.watch_id] = _DispatchLease(
                window_id=marker.window_id,
                owner_id=owner_id,
                generation=generation,
                expires_at=expires,
            )
            return DispatchResult(
                status=DispatchStatus.CLAIMED,
                claim=DispatchClaim(
                    watch_id=marker.watch_id,
                    window_id=marker.window_id,
                    scheduled_for=current.scheduled_for,
                    owner_id=owner_id,
                    generation=generation,
                    lease_expires_at=expires,
                ),
            )

    async def mark_dispatched(
        self,
        claim: DispatchClaim,
        redispatch_after: datetime,
    ) -> bool:
        async with self._lock:
            held = self._dispatch_leases.get(claim.watch_id)
            if held is None or not self._owns_dispatch(claim, held):
                return False
            # Deferral is expressed as the lease surviving until the grace time,
            # leaving the schedule marker (the logical due time) untouched.
            held.expires_at = redispatch_after
            return True

    async def release_dispatch(self, claim: DispatchClaim) -> bool:
        async with self._lock:
            held = self._dispatch_leases.get(claim.watch_id)
            if held is not None and self._owns_dispatch(claim, held):
                del self._dispatch_leases[claim.watch_id]
                return True
            return False

    @staticmethod
    def _owns_dispatch(claim: DispatchClaim, held: _DispatchLease) -> bool:
        return (
            held.generation == claim.generation
            and held.owner_id == claim.owner_id
        )

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
        *,
        ignore_schedule: bool = False,
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
            if (
                not ignore_schedule
                and runtime.scheduled_for is not None
                and runtime.scheduled_for > now
            ):
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
            # Holding the claim *is* the ownership signal; the runtime is left
            # unchanged until begin_booking or commit, which keeps this in step
            # with the Redis script that writes only the claim key.
            return ClaimResult(
                status=ClaimStatus.OWNED,
                claim=WindowClaim(
                    watch=watch,
                    runtime=runtime,
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
        self._dispatch_leases.pop(watch_id, None)
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

    #: Lua sources registered lazily, so a client that only serves the legacy
    #: document surface never needs scripting support.
    _SCRIPT_SOURCES = {
        "create": watch_scripts.CREATE_WITH_SCHEDULE,
        "claim": watch_scripts.CLAIM_WINDOW,
        "begin_booking": watch_scripts.BEGIN_BOOKING,
        "commit": watch_scripts.COMMIT_WINDOW,
        "cancel": watch_scripts.CANCEL_IF_ACTIVE,
        "expire": watch_scripts.EXPIRE_IF_ELIGIBLE,
        "release": watch_scripts.RELEASE_CLAIM,
        "claim_dispatch": watch_scripts.CLAIM_DISPATCH,
        "mark_dispatched": watch_scripts.MARK_DISPATCHED,
        "release_dispatch": watch_scripts.RELEASE_DISPATCH,
    }

    def __init__(
        self,
        client: Any,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._scripts: dict[str, Any] = {}

    def _script(self, name: str) -> Any:
        script = self._scripts.get(name)
        if script is None:
            script = self._client.register_script(self._SCRIPT_SOURCES[name])
            self._scripts[name] = script
        return script

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
        pipeline.delete(self._runtime_key(watch_id))
        pipeline.delete(self._fence_key(watch_id))
        pipeline.delete(self._claim_key(watch_id))
        pipeline.srem(INDEX_KEY, watch_id)
        pipeline.srem(ACTIVE_INDEX_KEY, watch_id)
        pipeline.zrem(SCHEDULE_INDEX_KEY, watch_id)
        pipeline.zrem(TERMINAL_INDEX_KEY, watch_id)
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

    @staticmethod
    def _runtime_key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}:runtime"

    @staticmethod
    def _fence_key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}:fence"

    @staticmethod
    def _claim_key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}:claim"

    @staticmethod
    def _dispatch_fence_key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}:dispfence"

    @staticmethod
    def _dispatch_key(watch_id: str, window_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}:dispatch:{dispatch_window_hash(window_id)}"

    # -- atomic state-machine surface ---------------------------------------

    async def get_runtime(self, watch_id: str) -> WatchRuntime | None:
        raw = await self._client.get(self._runtime_key(watch_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return WatchRuntime.model_validate_json(raw)
        except ValidationError:
            return None

    async def schedule_marker(self, watch_id: str) -> ScheduleMarker | None:
        score = await self._client.zscore(SCHEDULE_INDEX_KEY, watch_id)
        if score is None:
            return None
        runtime = await self.get_runtime(watch_id)
        if runtime is None or runtime.window_id is None:
            return None
        return ScheduleMarker(
            watch_id=watch_id,
            window_id=runtime.window_id,
            scheduled_for=_from_ms(int(score)),
        )

    async def due_schedule_markers(
        self,
        now: datetime,
        horizon_seconds: float,
        limit: int,
    ) -> list[ScheduleMarker]:
        cutoff_ms = _to_ms(now + timedelta(seconds=horizon_seconds))
        rows = await self._client.zrangebyscore(
            SCHEDULE_INDEX_KEY,
            min="-inf",
            max=cutoff_ms,
            start=0,
            num=max(0, limit),
            withscores=True,
        )
        markers: list[ScheduleMarker] = []
        for member, _score in rows:
            watch_id = _text(member)
            runtime = await self.get_runtime(watch_id)
            # A member without a live current window is a stale index entry;
            # recovery prunes it. The logical due time comes from the runtime,
            # not the score, so it matches the in-memory store bit for bit.
            if runtime is None or runtime.window_id is None:
                continue
            if runtime.scheduled_for is None:
                continue
            markers.append(
                ScheduleMarker(
                    watch_id=watch_id,
                    window_id=runtime.window_id,
                    scheduled_for=runtime.scheduled_for,
                )
            )
        return markers

    async def claim_dispatch(
        self,
        marker: ScheduleMarker,
        owner_id: str,
        lease_seconds: float,
    ) -> DispatchResult:
        out = await self._script('claim_dispatch')(
            keys=[
                self._key(marker.watch_id),
                self._runtime_key(marker.watch_id),
                self._dispatch_fence_key(marker.watch_id),
                self._dispatch_key(marker.watch_id, marker.window_id),
                SCHEDULE_INDEX_KEY,
            ],
            args=[
                marker.watch_id,
                marker.window_id,
                owner_id,
                str(int(lease_seconds * 1000)),
                str(self._now_ms()),
            ],
        )
        code = _code(out)
        if code != "CLAIMED":
            return DispatchResult(status=DispatchStatus(code))
        return DispatchResult(
            status=DispatchStatus.CLAIMED,
            claim=DispatchClaim(
                watch_id=marker.watch_id,
                window_id=marker.window_id,
                scheduled_for=marker.scheduled_for,
                owner_id=owner_id,
                generation=int(out[1]),
                lease_expires_at=_from_ms(int(out[2])),
            ),
        )

    async def mark_dispatched(
        self,
        claim: DispatchClaim,
        redispatch_after: datetime,
    ) -> bool:
        out = await self._script('mark_dispatched')(
            keys=[self._dispatch_key(claim.watch_id, claim.window_id)],
            args=[
                claim.owner_id,
                str(claim.generation),
                str(_to_ms(redispatch_after)),
                str(self._now_ms()),
            ],
        )
        return bool(int(out))

    async def release_dispatch(self, claim: DispatchClaim) -> bool:
        out = await self._script('release_dispatch')(
            keys=[self._dispatch_key(claim.watch_id, claim.window_id)],
            args=[claim.owner_id, str(claim.generation)],
        )
        return bool(int(out))

    async def create_with_schedule(
        self,
        watch: Watch,
        runtime: WatchRuntime,
    ) -> CreateResult:
        scheduled_ms = ""
        if runtime.window_id is not None and runtime.scheduled_for is not None:
            scheduled_ms = str(_to_ms(runtime.scheduled_for))
        out = await self._script('create')(
            keys=[
                self._key(watch.watch_id),
                self._runtime_key(watch.watch_id),
                self._fence_key(watch.watch_id),
                INDEX_KEY,
                ACTIVE_INDEX_KEY,
                SCHEDULE_INDEX_KEY,
            ],
            args=[
                watch.watch_id,
                watch.model_dump_json(),
                runtime.model_dump_json(),
                scheduled_ms,
            ],
        )
        if _code(out) == "CREATED":
            return CreateResult(
                status=CreateStatus.CREATED, watch=watch, runtime=runtime
            )
        return CreateResult(
            status=CreateStatus.ALREADY_EXISTS,
            watch=Watch.model_validate_json(_text(out[1])),
            runtime=WatchRuntime.model_validate_json(_text(out[2])),
        )

    async def claim_window(
        self,
        watch_id: str,
        window_id: str,
        owner_id: str,
        lease_seconds: float,
        *,
        ignore_schedule: bool = False,
    ) -> ClaimResult:
        now_ms = self._now_ms()
        out = await self._script('claim')(
            keys=[
                self._key(watch_id),
                self._runtime_key(watch_id),
                self._fence_key(watch_id),
                self._claim_key(watch_id),
                SCHEDULE_INDEX_KEY,
            ],
            args=[
                watch_id,
                window_id,
                owner_id,
                str(int(lease_seconds * 1000)),
                str(now_ms),
                "1" if ignore_schedule else "0",
            ],
        )
        code = _code(out)
        if code != "OWNED":
            return ClaimResult(status=ClaimStatus(code))
        return ClaimResult(
            status=ClaimStatus.OWNED,
            claim=WindowClaim(
                watch=Watch.model_validate_json(_text(out[1])),
                runtime=WatchRuntime.model_validate_json(_text(out[2])),
                owner_id=owner_id,
                window_id=window_id,
                token=int(out[3]),
                lease_expires_at=_from_ms(int(out[4])),
            ),
        )

    async def begin_booking(self, claim: WindowClaim) -> BookingPermit:
        booking_runtime = claim.runtime.model_copy(
            update={"phase": RuntimePhase.BOOKING}
        )
        out = await self._script('begin_booking')(
            keys=[
                self._key(claim.watch.watch_id),
                self._runtime_key(claim.watch.watch_id),
                self._claim_key(claim.watch.watch_id),
            ],
            args=[
                claim.watch.watch_id,
                str(claim.runtime.revision),
                str(claim.token),
                claim.owner_id,
                booking_runtime.model_dump_json(),
                str(self._now_ms()),
            ],
        )
        code = _code(out)
        if code == "GRANTED":
            return BookingPermit(
                status=BookingPermitStatus.GRANTED,
                permit_id=f"{claim.watch.watch_id}:{claim.token}",
            )
        return BookingPermit(status=BookingPermitStatus(code))

    async def commit_window(
        self,
        claim: WindowClaim,
        new_watch: Watch,
        new_runtime: WatchRuntime,
    ) -> CommitResult:
        watch_id = claim.watch.watch_id
        stored_runtime = new_runtime.model_copy(
            update={"revision": claim.runtime.revision + 1, "phase": None}
        )
        is_terminal = new_watch.status.is_terminal
        next_scheduled_ms = ""
        if (
            not is_terminal
            and stored_runtime.window_id is not None
            and stored_runtime.scheduled_for is not None
        ):
            next_scheduled_ms = str(_to_ms(stored_runtime.scheduled_for))
        terminal_delete_ms = ""
        if is_terminal and stored_runtime.terminal_delete_at is not None:
            terminal_delete_ms = str(_to_ms(stored_runtime.terminal_delete_at))
        event_id = ""
        if is_terminal and new_watch.status in _TERMINAL_EVENT_STATUSES:
            event_id = terminal_event_id(
                watch_id, new_watch.status, stored_runtime.revision
            )
        out = await self._script('commit')(
            keys=[
                self._key(watch_id),
                self._runtime_key(watch_id),
                self._claim_key(watch_id),
                INDEX_KEY,
                ACTIVE_INDEX_KEY,
                SCHEDULE_INDEX_KEY,
                TERMINAL_INDEX_KEY,
                EVENTS_KEY,
            ],
            args=[
                watch_id,
                str(claim.runtime.revision),
                str(claim.token),
                claim.owner_id,
                new_watch.model_dump_json(),
                stored_runtime.model_dump_json(),
                "1" if is_terminal else "0",
                next_scheduled_ms,
                terminal_delete_ms,
                event_id,
                "",  # retention PEXPIREAT wired in the retention phase
                str(self._now_ms()),
            ],
        )
        code = _code(out)
        if code != "COMMITTED":
            return CommitResult(status=CommitStatus(code))
        returned_event = out[2] if len(out) > 2 and out[2] else None
        return CommitResult(
            status=CommitStatus.COMMITTED,
            watch=new_watch,
            event_id=returned_event,
        )

    async def cancel_if_active(self, watch_id: str) -> TransitionResult:
        for _attempt in range(5):
            watch = await self.get(watch_id)
            if watch is None:
                return TransitionResult(TransitionStatus.UNKNOWN)
            if watch.status.is_terminal:
                return TransitionResult(TransitionStatus.NOOP, watch=watch)
            runtime = await self.get_runtime(watch_id)
            cas_revision = "" if runtime is None else str(runtime.revision)

            if runtime is not None and runtime.phase is RuntimePhase.BOOKING:
                pending = runtime.model_copy(update={"cancel_requested": True})
                out = await self._script('cancel')(
                    keys=self._cancel_keys(watch_id),
                    args=[
                        watch_id,
                        cas_revision,
                        "pending",
                        "",
                        pending.model_dump_json(),
                    ],
                )
            else:
                now = self._clock()
                cancelled = watch.model_copy(
                    update={
                        "status": WatchStatus.CANCELLED,
                        "next_check_at": None,
                        "updated_at": now,
                    }
                )
                new_runtime_json = ""
                if runtime is not None:
                    new_runtime_json = runtime.model_copy(
                        update={
                            "revision": runtime.revision + 1,
                            "window_id": None,
                            "scheduled_for": None,
                            "phase": None,
                            "cancel_requested": False,
                        }
                    ).model_dump_json()
                out = await self._script('cancel')(
                    keys=self._cancel_keys(watch_id),
                    args=[
                        watch_id,
                        cas_revision,
                        "cancel",
                        cancelled.model_dump_json(),
                        new_runtime_json,
                    ],
                )

            code = _code(out)
            if code == "FENCED":
                continue  # a concurrent commit moved us on; re-read and retry
            if code == "APPLIED":
                return TransitionResult(
                    TransitionStatus.APPLIED,
                    watch=Watch.model_validate_json(_text(out[1])),
                )
            if code == "NOT_ELIGIBLE":
                return TransitionResult(TransitionStatus.NOT_ELIGIBLE, watch=watch)
            if code == "NOOP":
                return TransitionResult(
                    TransitionStatus.NOOP,
                    watch=Watch.model_validate_json(_text(out[1])),
                )
            return TransitionResult(TransitionStatus.UNKNOWN)
        return TransitionResult(TransitionStatus.FENCED)

    async def expire_if_eligible(
        self,
        watch_id: str,
        *,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> TransitionResult:
        watch = await self.get(watch_id)
        if watch is None:
            return TransitionResult(TransitionStatus.UNKNOWN)
        if watch.status.is_terminal:
            return TransitionResult(TransitionStatus.NOOP, watch=watch)
        runtime = await self.get_runtime(watch_id)
        if (
            expected_revision is not None
            and runtime is not None
            and runtime.revision != expected_revision
        ):
            return TransitionResult(TransitionStatus.FENCED)
        if not force and not watch.is_exhausted(self._clock()):
            return TransitionResult(TransitionStatus.NOT_ELIGIBLE, watch=watch)

        new_revision = (runtime.revision + 1) if runtime is not None else 0
        expired = watch.model_copy(
            update={
                "status": WatchStatus.EXPIRED,
                "next_check_at": None,
                "updated_at": self._clock(),
            }
        )
        new_runtime_json = ""
        if runtime is not None:
            new_runtime_json = runtime.model_copy(
                update={
                    "revision": new_revision,
                    "window_id": None,
                    "scheduled_for": None,
                    "phase": None,
                    "cancel_requested": False,
                }
            ).model_dump_json()
        event_id = terminal_event_id(
            watch_id, WatchStatus.EXPIRED, new_revision
        )
        out = await self._script('expire')(
            keys=[
                self._key(watch_id),
                self._runtime_key(watch_id),
                self._claim_key(watch_id),
                ACTIVE_INDEX_KEY,
                SCHEDULE_INDEX_KEY,
                TERMINAL_INDEX_KEY,
                EVENTS_KEY,
            ],
            args=[
                watch_id,
                "" if runtime is None else str(runtime.revision),
                expired.model_dump_json(),
                new_runtime_json,
                event_id,
                "",  # terminal_delete_ms wired in the retention phase
                "",  # retention PEXPIREAT wired in the retention phase
            ],
        )
        code = _code(out)
        if code == "APPLIED":
            return TransitionResult(
                TransitionStatus.APPLIED,
                watch=Watch.model_validate_json(_text(out[1])),
                event_id=event_id,
            )
        if code == "NOOP":
            return TransitionResult(
                TransitionStatus.NOOP,
                watch=Watch.model_validate_json(_text(out[1])),
            )
        if code == "FENCED":
            return TransitionResult(TransitionStatus.FENCED)
        return TransitionResult(TransitionStatus.UNKNOWN)

    async def release_claim(self, claim: WindowClaim) -> bool:
        out = await self._script('release')(
            keys=[self._claim_key(claim.watch.watch_id)],
            args=[claim.owner_id, str(claim.token)],
        )
        return bool(int(out))

    def _cancel_keys(self, watch_id: str) -> list[str]:
        return [
            self._key(watch_id),
            self._runtime_key(watch_id),
            self._claim_key(watch_id),
            ACTIVE_INDEX_KEY,
            SCHEDULE_INDEX_KEY,
        ]

    def _now_ms(self) -> int:
        return _to_ms(self._clock())
