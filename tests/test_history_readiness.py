"""Task 7: `history_readiness` -- the evidence-based projection health signal.

Two layers: `ReadinessTracker.record_history_outcome` in isolation (state
transitions match the queue/recovery signals' evidence-only philosophy), and
`TrackingHistoryRecorder` wired in front of a real repository (every
underlying `record()` outcome updates the tracker and passes through
unchanged, including on failure so `WatchService`'s existing try/except still
sees the same exception).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watch_history import (
    TrackingHistoryRecorder,
    WatchHistoryRecorder,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.readiness import Readiness, ReadinessTracker


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def _watch(watch_id: str = "watch_1") -> Watch:
    return Watch(
        watch_id=watch_id,
        status=WatchStatus.ACTIVE,
        query=_query(),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=NOW,
    )


# ---------------------------------------------------------------------------
# ReadinessTracker.record_history_outcome in isolation.
# ---------------------------------------------------------------------------


def test_a_fresh_tracker_reports_history_readiness_unknown() -> None:
    assert ReadinessTracker().history_readiness is Readiness.UNKNOWN


def test_a_successful_projection_write_marks_history_ready() -> None:
    tracker = ReadinessTracker()

    tracker.record_history_outcome(ok=True)

    assert tracker.history_readiness is Readiness.READY


def test_a_failed_projection_write_marks_history_degraded() -> None:
    tracker = ReadinessTracker()

    tracker.record_history_outcome(ok=False)

    assert tracker.history_readiness is Readiness.DEGRADED


def test_a_later_success_recovers_from_a_prior_degradation() -> None:
    tracker = ReadinessTracker()
    tracker.record_history_outcome(ok=False)
    assert tracker.history_readiness is Readiness.DEGRADED

    tracker.record_history_outcome(ok=True)

    assert tracker.history_readiness is Readiness.READY


def test_history_readiness_does_not_bleed_into_queue_or_recovery() -> None:
    tracker = ReadinessTracker()

    tracker.record_history_outcome(ok=True)

    assert tracker.queue_readiness is Readiness.UNKNOWN
    assert tracker.recovery_readiness is Readiness.UNKNOWN


# ---------------------------------------------------------------------------
# TrackingHistoryRecorder decorator around a WatchHistoryRecorder.
# ---------------------------------------------------------------------------


class _RecordingHistory:
    """Passes through unchanged and lets the test assert against it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def record(self, watch: Watch, owner_client_id: str | None = None) -> None:
        self.calls.append((watch.watch_id, owner_client_id))


class _RaisingHistory:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.attempts = 0

    async def record(self, watch: Watch, owner_client_id: str | None = None) -> None:
        self.attempts += 1
        raise self.error


def test_a_successful_underlying_record_marks_readiness_ready() -> None:
    tracker = ReadinessTracker()
    recorder = TrackingHistoryRecorder(_RecordingHistory(), tracker)

    asyncio.run(recorder.record(_watch(), owner_client_id="visitor-1"))

    assert tracker.history_readiness is Readiness.READY


def test_the_decorator_passes_the_call_through_unchanged() -> None:
    inner = _RecordingHistory()
    recorder = TrackingHistoryRecorder(inner, ReadinessTracker())

    asyncio.run(recorder.record(_watch("watch_x"), owner_client_id="visitor-1"))

    assert inner.calls == [("watch_x", "visitor-1")]


def test_a_failing_underlying_record_marks_readiness_degraded_and_re_raises() -> None:
    tracker = ReadinessTracker()
    inner = _RaisingHistory(RuntimeError("Postgres is unreachable"))
    recorder = TrackingHistoryRecorder(inner, tracker)

    with pytest.raises(RuntimeError, match="Postgres is unreachable"):
        asyncio.run(recorder.record(_watch(), owner_client_id="visitor-1"))

    assert tracker.history_readiness is Readiness.DEGRADED
    assert inner.attempts == 1


def test_a_later_success_after_a_failure_recovers_readiness() -> None:
    tracker = ReadinessTracker()
    failing = _RaisingHistory(RuntimeError("outage"))
    with pytest.raises(RuntimeError):
        asyncio.run(TrackingHistoryRecorder(failing, tracker).record(_watch()))
    assert tracker.history_readiness is Readiness.DEGRADED

    ok = _RecordingHistory()
    asyncio.run(TrackingHistoryRecorder(ok, tracker).record(_watch()))

    assert tracker.history_readiness is Readiness.READY


def test_tracking_recorder_satisfies_the_watch_history_recorder_protocol() -> None:
    """A structural check: the decorator can be passed anywhere the
    `WatchHistoryRecorder` protocol is expected, so `WatchService` accepts it
    without any special casing."""

    recorder: WatchHistoryRecorder = TrackingHistoryRecorder(
        _RecordingHistory(), ReadinessTracker()
    )

    assert callable(recorder.record)
