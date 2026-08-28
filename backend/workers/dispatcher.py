"""Publishes durable schedule markers to the task queue under a finite lease.

A watch's next availability check is committed to the repository as a durable
schedule marker *before* any broker message is sent, because Redis state and a
Celery broker cannot share one atomic commit. This dispatcher is the bridge:
it reads the markers that have entered the dispatch horizon, takes a
single-flight per-window lease so racing replicas do not each publish the same
marker, publishes the poll, and records broker acceptance by deferring the
marker's redispatch until a recovery grace elapses. A publish failure releases
the lease and leaves the marker due, so a worker retry or startup reconciliation
picks it up again.

Only the horizon read and single dispatch pass live here. The continuous sweep
loop, wake computation, and leader lease are wired in with the recovery
coordinator, which owns the running-application lifecycle.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import logging
from typing import Any

from backend.db.repositories.watch_decisions import (
    DispatchClaim,
    DispatchStatus,
    ScheduleMarker,
)


logger = logging.getLogger(__name__)


class _Outcome(Enum):
    DISPATCHED = "dispatched"
    DEFERRED = "deferred"
    FAILED = "failed"

#: How long a single-flight dispatch lease fences other dispatchers off one
#: window while this pass publishes it. Short: it only has to cover the enqueue
#: call, after which acceptance extends it to the recovery grace. Wired to a
#: dedicated setting when the recovery coordinator lands.
_DEFAULT_DISPATCH_LEASE_SECONDS = 30.0

#: After a successful publish, how long before the same window may be published
#: again. This absorbs a lost broker message: recovery redispatches only once
#: the grace passes, while a healthy delivery consumes the marker well before.
_DEFAULT_RECOVERY_GRACE_SECONDS = 60.0

#: Markers published per pass. Bounds the work one sweep does regardless of how
#: far behind the schedule is; the remainder is taken on the next pass.
_DEFAULT_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
class DispatchSweepResult:
    """What one `dispatch_due` pass did, for readiness and observability."""

    considered: int
    dispatched: int
    #: BUSY or STALE: another dispatcher owns it, or the window moved on.
    deferred: int
    failed: int

    @property
    def has_backlog(self) -> bool:
        """Whether a marker was left undispatched by a failure this pass."""

        return self.failed > 0


class WatchScheduleDispatcher:
    """Turns due schedule markers into single-flight queue publications."""

    def __init__(
        self,
        repository: Any,
        queue: Any,
        *,
        owner_id: str,
        horizon_seconds: float,
        lease_seconds: float = _DEFAULT_DISPATCH_LEASE_SECONDS,
        recovery_grace_seconds: float = _DEFAULT_RECOVERY_GRACE_SECONDS,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._owner_id = owner_id
        self._horizon_seconds = horizon_seconds
        self._lease_seconds = lease_seconds
        self._recovery_grace_seconds = recovery_grace_seconds
        self._batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_due(self) -> DispatchSweepResult:
        """Publish every marker now within the horizon, at most once each."""

        now = self._clock()
        markers = await self._repository.due_schedule_markers(
            now, self._horizon_seconds, self._batch_size
        )
        dispatched = deferred = failed = 0
        for marker in markers:
            outcome = await self._dispatch_one(marker, now)
            if outcome is _Outcome.DISPATCHED:
                dispatched += 1
            elif outcome is _Outcome.DEFERRED:
                deferred += 1
            else:
                failed += 1
        return DispatchSweepResult(
            considered=len(markers),
            dispatched=dispatched,
            deferred=deferred,
            failed=failed,
        )

    async def _dispatch_one(
        self,
        marker: ScheduleMarker,
        now: datetime,
    ) -> _Outcome:
        claim_result = await self._repository.claim_dispatch(
            marker, self._owner_id, self._lease_seconds
        )
        if claim_result.status is not DispatchStatus.CLAIMED:
            # BUSY: another replica has it. STALE: a commit advanced the window
            # and its own marker will be dispatched under its own score.
            return _Outcome.DEFERRED
        claim = claim_result.claim
        assert claim is not None  # CLAIMED always carries the claim

        delay_seconds = max(0.0, (marker.scheduled_for - now).total_seconds())
        try:
            await self._queue.enqueue_watch_poll(
                marker.watch_id,
                window_id=marker.window_id,
                delay_seconds=delay_seconds,
                due_at=marker.scheduled_for,
                task_id=self._task_id(claim),
            )
        except Exception:
            # The two systems cannot agree atomically, so on any publish error
            # we release the lease and leave the marker due; retry/recovery
            # will try again. We never roll back the committed watch state.
            logger.exception(
                "watch poll dispatch failed",
                extra={
                    "watch_id": marker.watch_id,
                    "window_id": marker.window_id,
                    "owner_id": self._owner_id,
                    "generation": claim.generation,
                },
            )
            await self._repository.release_dispatch(claim)
            return _Outcome.FAILED

        redispatch_after = max(marker.scheduled_for, now) + timedelta(
            seconds=self._recovery_grace_seconds
        )
        await self._repository.mark_dispatched(claim, redispatch_after)
        return _Outcome.DISPATCHED

    @staticmethod
    def _task_id(claim: DispatchClaim) -> str:
        """Deterministic Celery id from the window and dispatch generation.

        Stable per (window, generation) so a duplicate physical publication of
        the *same* generation reuses the id; this is a debugging/idempotency
        aid for idempotent transports, not a claim of broker deduplication.
        """

        return f"dibs-poll:{claim.window_id}:{claim.generation}"
