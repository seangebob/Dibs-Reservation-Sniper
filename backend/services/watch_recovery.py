"""Startup and follow-up reconciliation of persisted watch schedules.

`_attach_redis` selects the store, queue, and mock state but never looks at the
work already durably scheduled: a watch whose successor window was committed
just before a crash, an overdue window, an exhausted watch that never expired, a
legacy record with no marker, or a stale active-index member left by a partial
write. The `RecoveryCoordinator` is what closes that gap. After the final
components are bound it reconciles every active-index member, dispatches the
markers that are now due through the same single-flight dispatcher normal
polling uses, and runs bounded terminal/mock cleanup.

Coordination is deliberately two-tier. In Redis mode a finite compare-owner
leader lease (`dibs:recovery:leader`) elects one scanner among the replicas; a
replica that loses the lease stops scanning at once, and the per-window dispatch
lease remains the final idempotency boundary even if two scanners briefly
overlap. In memory mode there is nothing to coordinate across processes, so a
process-local lock serializes passes and the coordinator makes no claim that
process-local state survives a restart.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import logging
from typing import Any

from backend.db.repositories.watch_decisions import (
    RecoveryCandidate,
    TransitionStatus,
)
from backend.models.watch_runtime import (
    WatchRuntime,
    migrate_legacy_watch,
    window_id_for,
)


logger = logging.getLogger(__name__)

#: Terminal watches removed per bounded cleanup batch during a leader pass. The
#: remainder is drained by follow-up sweeps, so no single pass is unbounded.
_DEFAULT_CLEANUP_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What one reconciliation pass observed, for readiness and diagnostics."""

    is_leader: bool
    considered: int = 0
    pruned: int = 0
    expired: int = 0
    synthesized: int = 0
    dispatched: int = 0
    failed: int = 0
    cleanup_removed: int = 0
    #: True while due markers could not all be dispatched or terminal cleanup
    #: has more due entries; readiness stays degraded until it clears.
    backlog: bool = False

    @property
    def ready(self) -> bool:
        """A leader pass that completed with nothing left undone."""

        return self.is_leader and self.failed == 0 and not self.backlog


class RecoveryCoordinator:
    """Reconciles persisted schedules under a finite, resumable ownership."""

    def __init__(
        self,
        repository: Any,
        dispatcher: Any,
        *,
        owner_id: str,
        distributed: bool,
        leader_lease_seconds: float,
        earliest_delay_seconds: float,
        mock_state: Any | None = None,
        cleanup_batch_size: int = _DEFAULT_CLEANUP_BATCH_SIZE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher
        self._owner_id = owner_id
        self._distributed = distributed
        self._leader_lease_seconds = leader_lease_seconds
        self._earliest_delay_seconds = earliest_delay_seconds
        self._mock_state = mock_state
        self._cleanup_batch_size = cleanup_batch_size
        self._clock = clock or (lambda: datetime.now(UTC))
        # Serializes passes within one process (the only coordination memory
        # mode has, and a guard against overlapping sweeps in Redis mode too).
        self._pass_lock = asyncio.Lock()

    async def reconcile_once(self) -> RecoveryOutcome:
        """Run one bounded pass, holding leadership for its duration."""

        async with self._pass_lock:
            try:
                leading = await self._hold_leadership()
            except Exception:
                # A leader-lease call that cannot even complete (an unreachable
                # or topology-incompatible Redis) must degrade, not crash the
                # caller -- this runs from application startup and a periodic
                # background loop, neither of which may raise out of a pass.
                logger.exception("recovery leadership acquisition failed")
                return RecoveryOutcome(is_leader=False, failed=1, backlog=True)
            if not leading:
                # Another replica leads; per-window dispatch leases keep an
                # accidental overlap safe, so we simply do not scan this pass.
                return RecoveryOutcome(is_leader=False)
            return await self._run_pass()

    async def release(self) -> None:
        """Give up leadership on shutdown so another replica resumes at once."""

        if self._distributed:
            try:
                await self._repository.release_leadership(self._owner_id)
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.warning("recovery leader release failed", exc_info=True)

    async def _hold_leadership(self) -> bool:
        if not self._distributed:
            return True
        # Renewal keeps an existing leader in place across passes; acquisition
        # takes a vacant lease. A replica that holds neither is not the leader.
        if await self._repository.renew_leadership(
            self._owner_id, self._leader_lease_seconds
        ):
            return True
        return await self._repository.acquire_leadership(
            self._owner_id, self._leader_lease_seconds
        )

    async def _run_pass(self) -> RecoveryOutcome:
        now = self._clock()
        pruned = expired = synthesized = failed = 0
        try:
            candidates = await self._repository.list_recovery_candidates()
        except Exception:
            logger.exception("recovery candidate listing failed")
            return RecoveryOutcome(is_leader=True, failed=1, backlog=True)

        for candidate in candidates:
            try:
                action = await self._reconcile_one(candidate, now)
            except Exception:
                failed += 1
                # A single bad record must never abort the remaining candidates.
                logger.warning(
                    "recovery candidate failed",
                    extra={
                        "watch_id": candidate.watch_id,
                        "owner_id": self._owner_id,
                    },
                    exc_info=True,
                )
                continue
            if action == "pruned":
                pruned += 1
            elif action == "expired":
                expired += 1
            elif action == "synthesized":
                synthesized += 1

        dispatched, dispatch_backlog = await self._dispatch_due()
        cleanup_removed, cleanup_backlog = await self._run_cleanup(now)

        return RecoveryOutcome(
            is_leader=True,
            considered=len(candidates),
            pruned=pruned,
            expired=expired,
            synthesized=synthesized,
            dispatched=dispatched,
            failed=failed,
            cleanup_removed=cleanup_removed,
            backlog=(
                failed > 0 or dispatch_backlog or cleanup_backlog
            ),
        )

    async def _reconcile_one(
        self, candidate: RecoveryCandidate, now: datetime
    ) -> str:
        watch = candidate.watch
        if watch is None:
            # Missing or corrupt document: fail closed and prune it entirely so
            # it stops appearing as active work on every subsequent read.
            await self._repository.prune_index_member(
                candidate.watch_id, from_all=True
            )
            return "pruned"
        if watch.status.is_terminal:
            # Terminal record left in the active index: drop only the active
            # membership; retention keeps the document and its cleanup entry.
            await self._repository.prune_index_member(
                candidate.watch_id, from_all=False
            )
            return "pruned"
        if watch.is_exhausted(now):
            result = await self._repository.expire_if_eligible(candidate.watch_id)
            if result.status is TransitionStatus.APPLIED:
                return "expired"
            return "noop"
        if candidate.has_live_claim:
            # An unexpired poll owner is finishing this window; leave its marker
            # deferred and let the claim complete or expire before any repair.
            return "deferred"
        if not candidate.has_marker:
            runtime = self._marker_runtime(candidate, now)
            if runtime is not None and await self._repository.synthesize_marker(
                candidate.watch_id, runtime
            ):
                return "synthesized"
            return "noop"
        # A future or due marker with no live claim is durable and correct; the
        # dispatcher publishes it exactly when it enters the horizon.
        return "preserved"

    def _marker_runtime(
        self, candidate: RecoveryCandidate, now: datetime
    ) -> WatchRuntime | None:
        """The runtime to persist when repairing a missing marker.

        The due time is never scheduled past `expires_at`, so recovery cannot
        resurrect provider work for a watch whose lifetime has already ended.
        """

        watch = candidate.watch
        assert watch is not None  # callers exclude the missing-document case
        if candidate.runtime is not None:
            runtime = candidate.runtime
            window = runtime.window_id or window_id_for(
                candidate.watch_id, runtime.cadence_sequence
            )
            base = runtime.scheduled_for or watch.next_check_at or now
            return runtime.model_copy(
                update={
                    "window_id": window,
                    "scheduled_for": min(base, watch.expires_at),
                }
            )
        # A legacy record with no sidecar is migrated into the coordinated
        # protocol without inflating its persisted attempt ceiling.
        runtime = migrate_legacy_watch(
            watch,
            earliest_delay_seconds=self._earliest_delay_seconds,
            now=now,
        )
        if runtime.window_id is None or runtime.scheduled_for is None:
            return None
        return runtime.model_copy(
            update={"scheduled_for": min(runtime.scheduled_for, watch.expires_at)}
        )

    async def _dispatch_due(self) -> tuple[int, bool]:
        try:
            sweep = await self._dispatcher.dispatch_due()
        except Exception:
            logger.exception("recovery dispatch sweep failed")
            return 0, True
        return sweep.dispatched, sweep.has_backlog

    async def _run_cleanup(self, now: datetime) -> tuple[int, bool]:
        removed = 0
        backlog = False
        try:
            result = await self._repository.cleanup_due(
                now, self._cleanup_batch_size
            )
            removed = result.removed
            backlog = result.remaining
        except Exception:
            logger.exception("recovery terminal cleanup failed")
            backlog = True
        if self._mock_state is not None:
            try:
                await self._mock_state.cleanup(now, self._cleanup_batch_size)
            except Exception:
                logger.exception("recovery mock-state cleanup failed")
                backlog = True
        return removed, backlog
