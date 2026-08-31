"""Evidence-based readiness for the watch queue and recovery coordinator.

`/health` must report `ready`, `degraded`, or `unknown` derived only from
checks the application actually performed -- never inferred from what is
merely configured or importable. This tracker is the single place that
records those performed observations (a broker dispatch attempt, a recovery
reconciliation pass) so `/health` reads evidence instead of guessing readiness
from component selection.

Asyncio queue readiness is deliberately NOT tracked here: it is a live,
always-knowable property of the bound queue object (open or closed on the
running loop), so the health route reads it directly rather than through a
possibly-stale recorded observation.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol


__all__ = ["Readiness", "ReadinessTracker"]


class Readiness(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class _RecoveryOutcomeLike(Protocol):
    """The subset of `RecoveryOutcome` this tracker reads.

    Structural, not a hard import of `watch_recovery`'s module, so the
    tracker's dependency on that shape stays legible and mypy still accepts
    the real `RecoveryOutcome` passed in from callers without an import cycle.
    """

    @property
    def is_leader(self) -> bool: ...

    @property
    def ready(self) -> bool: ...

    @property
    def failed(self) -> int: ...

    @property
    def dispatched(self) -> int: ...

    @property
    def dispatch_failed(self) -> int: ...


class ReadinessTracker:
    """Records the last performed queue-dispatch and recovery observations."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue_state = Readiness.UNKNOWN
        self._queue_observed_at: datetime | None = None
        self._recovery_state = Readiness.UNKNOWN
        self._recovery_observed_at: datetime | None = None
        #: Whether this process has ever held the recovery leader lease, so a
        #: later `is_leader=False` can be told apart from "never contended".
        self._was_leader = False
        self._history_state = Readiness.UNKNOWN
        self._history_observed_at: datetime | None = None

    def record_dispatch_outcome(self, *, dispatched: int, failed: int) -> None:
        """Record what one broker-dispatch attempt actually did.

        A pass with nothing due performs no broker call at all, so it leaves
        readiness exactly as it was rather than manufacturing evidence from
        an empty schedule.
        """

        if failed > 0:
            self._queue_state = Readiness.DEGRADED
            self._queue_observed_at = self._clock()
        elif dispatched > 0:
            self._queue_state = Readiness.READY
            self._queue_observed_at = self._clock()

    def record_recovery_outcome(self, outcome: _RecoveryOutcomeLike) -> None:
        """Record what one reconciliation pass observed.

        Ready only after a complete leader pass with nothing left undone;
        degraded after a failed candidate/dispatch/cleanup, a due backlog, or
        a leadership check/renewal that could not itself complete; otherwise
        left as-is, since a clean non-leader pass is not evidence either way
        about this process's own recovery health.
        """

        self._recovery_observed_at = self._clock()
        if outcome.is_leader:
            self._was_leader = True
            self._recovery_state = (
                Readiness.READY if outcome.ready else Readiness.DEGRADED
            )
        elif outcome.failed > 0 or self._was_leader:
            # Either the leadership check itself errored, or this process
            # held the lease and has now lost it -- both are degradations of
            # this process's own recovery health, not a benign hand-off.
            self._recovery_state = Readiness.DEGRADED
        self.record_dispatch_outcome(
            dispatched=outcome.dispatched, failed=outcome.dispatch_failed
        )

    def record_history_outcome(self, *, ok: bool) -> None:
        """Record what one history-projection write actually did.

        Ready on any successful `WatchHistoryRepository.record(...)`;
        degraded on any failure. Unlike queue/recovery, "no attempt made" is
        NOT a real state to preserve here -- writes are triggered directly by
        watch outcomes, and a period with no watch outcomes correctly leaves
        the last observation standing rather than being manufactured evidence.
        """

        self._history_state = Readiness.READY if ok else Readiness.DEGRADED
        self._history_observed_at = self._clock()

    @property
    def queue_readiness(self) -> Readiness:
        return self._queue_state

    @property
    def recovery_readiness(self) -> Readiness:
        return self._recovery_state

    @property
    def history_readiness(self) -> Readiness:
        return self._history_state
