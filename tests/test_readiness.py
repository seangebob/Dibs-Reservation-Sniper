"""`ReadinessTracker`: evidence-based queue/recovery readiness derivation.

Every state transition is driven only by a performed observation -- a
dispatch outcome or a recovery pass -- never by configuration or import
success, matching the `/health` contract in the milestone-3 design.
"""

from backend.services.readiness import Readiness, ReadinessTracker
from backend.services.watch_recovery import RecoveryOutcome


def _outcome(**overrides: object) -> RecoveryOutcome:
    fields: dict[str, object] = {"is_leader": True}
    fields.update(overrides)
    return RecoveryOutcome(**fields)  # type: ignore[arg-type]


def test_a_fresh_tracker_reports_unknown_for_both() -> None:
    tracker = ReadinessTracker()

    assert tracker.queue_readiness is Readiness.UNKNOWN
    assert tracker.recovery_readiness is Readiness.UNKNOWN


def test_a_successful_dispatch_marks_the_queue_ready() -> None:
    tracker = ReadinessTracker()

    tracker.record_dispatch_outcome(dispatched=1, failed=0)

    assert tracker.queue_readiness is Readiness.READY


def test_a_failed_dispatch_marks_the_queue_degraded() -> None:
    tracker = ReadinessTracker()

    tracker.record_dispatch_outcome(dispatched=0, failed=1)

    assert tracker.queue_readiness is Readiness.DEGRADED


def test_a_pass_with_nothing_due_leaves_queue_readiness_unchanged() -> None:
    tracker = ReadinessTracker()
    tracker.record_dispatch_outcome(dispatched=1, failed=0)

    tracker.record_dispatch_outcome(dispatched=0, failed=0)

    # An empty schedule performed no broker call, so it is not evidence either
    # way -- the prior observation stands.
    assert tracker.queue_readiness is Readiness.READY


def test_a_complete_leader_pass_marks_recovery_ready() -> None:
    tracker = ReadinessTracker()

    tracker.record_recovery_outcome(_outcome(is_leader=True, failed=0, backlog=False))

    assert tracker.recovery_readiness is Readiness.READY


def test_a_leader_pass_with_a_failed_candidate_marks_recovery_degraded() -> None:
    tracker = ReadinessTracker()

    tracker.record_recovery_outcome(_outcome(is_leader=True, failed=1))

    assert tracker.recovery_readiness is Readiness.DEGRADED


def test_a_leader_pass_with_due_backlog_marks_recovery_degraded() -> None:
    tracker = ReadinessTracker()

    tracker.record_recovery_outcome(
        _outcome(is_leader=True, failed=0, backlog=True)
    )

    assert tracker.recovery_readiness is Readiness.DEGRADED


def test_never_having_led_leaves_recovery_readiness_unknown() -> None:
    tracker = ReadinessTracker()

    # Another replica legitimately holds the lease; this process has no
    # evidence about its own recovery health from that alone.
    tracker.record_recovery_outcome(_outcome(is_leader=False, failed=0))

    assert tracker.recovery_readiness is Readiness.UNKNOWN


def test_a_leadership_check_that_cannot_complete_marks_recovery_degraded() -> None:
    tracker = ReadinessTracker()

    tracker.record_recovery_outcome(_outcome(is_leader=False, failed=1))

    assert tracker.recovery_readiness is Readiness.DEGRADED


def test_losing_a_previously_held_lease_marks_recovery_degraded() -> None:
    tracker = ReadinessTracker()
    tracker.record_recovery_outcome(_outcome(is_leader=True, failed=0, backlog=False))
    assert tracker.recovery_readiness is Readiness.READY

    tracker.record_recovery_outcome(_outcome(is_leader=False, failed=0))

    assert tracker.recovery_readiness is Readiness.DEGRADED


def test_a_later_clean_pass_recovers_from_a_degraded_state() -> None:
    tracker = ReadinessTracker()
    tracker.record_recovery_outcome(_outcome(is_leader=True, failed=1))
    assert tracker.recovery_readiness is Readiness.DEGRADED

    tracker.record_recovery_outcome(_outcome(is_leader=True, failed=0, backlog=False))

    assert tracker.recovery_readiness is Readiness.READY


def test_a_recovery_outcome_also_feeds_the_queue_dispatch_signal() -> None:
    tracker = ReadinessTracker()

    tracker.record_recovery_outcome(
        _outcome(is_leader=True, dispatched=3, dispatch_failed=0)
    )
    assert tracker.queue_readiness is Readiness.READY

    tracker.record_recovery_outcome(
        _outcome(is_leader=True, dispatched=0, dispatch_failed=1)
    )
    assert tracker.queue_readiness is Readiness.DEGRADED
