"""`WatchHistoryRecorder` wiring: a passive observer, never a participant.

Every assertion here checks two things together: the history repository was
called with the right watch, AND the triggering operation's own return value
and success are completely unaffected -- including when the history call
raises. That combination is the actual content of Requirement 3.2/6.3: history
recording must never influence the caller-visible outcome of a live watch
operation.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.base import AdapterError
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchPollOutcome, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue
from backend.workers.scheduler import PollSchedule


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
TARGET_DATE = "2026-09-05"


def query(venue_name: str = "Cote") -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name=venue_name,
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date=TARGET_DATE,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


class EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a watch keeps polling."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


class FailingAdapter(MockBookingAdapter):
    """Raises the way a provider outage would."""

    async def search_availability(self, query):  # noqa: ANN001
        raise AdapterError("provider is down")


class RecordingHistory:
    """Collects every `record()` call instead of writing anywhere."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, WatchStatus, str | None]] = []

    async def record(
        self, watch: Watch, owner_client_id: str | None = None, user_id=None
    ) -> None:
        self.calls.append((watch.watch_id, watch.status, owner_client_id))


class RaisingHistory:
    """Always fails, the way an unreachable Postgres would."""

    def __init__(self) -> None:
        self.attempts = 0

    async def record(
        self, watch: Watch, owner_client_id: str | None = None, user_id=None
    ) -> None:
        self.attempts += 1
        raise RuntimeError("Postgres is unreachable")


def build_service(
    adapter=None,
    *,
    repository=None,
    history=None,
    max_attempts: int = 10,
    auto_book: bool = False,
) -> tuple[WatchService, InMemoryWatchRepository]:
    repository = repository or InMemoryWatchRepository()
    service = WatchService(
        repository,
        adapter or EmptyAdapter(),
        RecordingTaskQueue(),
        schedule=PollSchedule(interval_seconds=180, jitter_seconds=30),
        history=history,
        max_attempts=max_attempts,
        clock=lambda: NOW,
    )
    return service, repository


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# No history configured: the default, and every pre-Milestone-4 test's setup.
# ---------------------------------------------------------------------------


def test_no_history_configured_is_the_default_and_nothing_breaks() -> None:
    service, _ = build_service()

    watch = _run(service.create(query()))

    assert watch.status is WatchStatus.ACTIVE


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_creating_a_watch_records_it_once_as_active() -> None:
    history = RecordingHistory()
    service, _ = build_service(history=history)

    watch = _run(service.create(query()))

    assert history.calls == [(watch.watch_id, WatchStatus.ACTIVE, None)]


def test_a_raising_history_does_not_affect_watch_creation() -> None:
    history = RaisingHistory()
    service, repository = build_service(history=history)

    watch = _run(service.create(query()))

    assert watch.status is WatchStatus.ACTIVE
    assert _run(repository.get(watch.watch_id)) == watch
    assert history.attempts == 1


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancelling_an_active_watch_records_the_cancelled_state() -> None:
    history = RecordingHistory()
    service, _ = build_service(history=history)
    watch = _run(service.create(query()))
    history.calls.clear()

    cancelled = _run(service.cancel(watch.watch_id))

    assert cancelled.status is WatchStatus.CANCELLED
    assert history.calls == [(watch.watch_id, WatchStatus.CANCELLED, None)]


def test_cancelling_an_unknown_watch_never_calls_history() -> None:
    history = RecordingHistory()
    service, _ = build_service(history=history)

    result = _run(service.cancel("watch_ghost"))

    assert result is None
    assert history.calls == []


def test_a_raising_history_does_not_affect_cancellation() -> None:
    history = RaisingHistory()
    service, _ = build_service(history=history)
    watch = _run(service.create(query()))

    cancelled = _run(service.cancel(watch.watch_id))

    assert cancelled.status is WatchStatus.CANCELLED


# ---------------------------------------------------------------------------
# Polling: miss/reschedule, found, booked, expired, outage
# ---------------------------------------------------------------------------


def test_a_no_availability_poll_records_the_rescheduled_watch() -> None:
    history = RecordingHistory()
    service, _ = build_service(history=history)
    watch = _run(service.create(query()))
    history.calls.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert history.calls == [(watch.watch_id, WatchStatus.ACTIVE, None)]


def test_a_found_poll_records_the_found_watch() -> None:
    history = RecordingHistory()
    adapter = MockBookingAdapter()
    service, _ = build_service(adapter=adapter, history=history)
    watch = _run(service.create(query()))
    history.calls.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert history.calls == [(watch.watch_id, WatchStatus.FOUND, None)]


def test_an_auto_book_poll_records_the_booked_watch() -> None:
    history = RecordingHistory()
    adapter = MockBookingAdapter()
    service, _ = build_service(adapter=adapter, history=history)
    watch = _run(service.create(query(), auto_book=True))
    history.calls.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.BOOKED
    assert history.calls == [(watch.watch_id, WatchStatus.BOOKED, None)]


def test_an_exhausted_poll_records_the_expired_watch() -> None:
    history = RecordingHistory()
    service, _ = build_service(history=history, max_attempts=1)
    watch = _run(service.create(query()))
    history.calls.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.EXPIRED
    assert history.calls == [(watch.watch_id, WatchStatus.EXPIRED, None)]


def test_a_provider_outage_records_the_backed_off_watch() -> None:
    history = RecordingHistory()
    service, _ = build_service(adapter=FailingAdapter(), history=history)
    watch = _run(service.create(query()))
    history.calls.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert history.calls == [(watch.watch_id, WatchStatus.ACTIVE, None)]


def test_a_raising_history_does_not_affect_a_poll_outcome() -> None:
    history = RaisingHistory()
    adapter = MockBookingAdapter()
    service, _ = build_service(adapter=adapter, history=history)
    watch = _run(service.create(query()))

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert history.attempts >= 1


# ---------------------------------------------------------------------------
# Legacy path: a watch saved without ever going through create_with_schedule
# has no runtime sidecar, so poll_once falls back to _legacy_poll_once.
# ---------------------------------------------------------------------------


def _legacy_watch(status: WatchStatus = WatchStatus.ACTIVE) -> Watch:
    return Watch(
        watch_id="watch_legacy",
        status=status,
        query=query(),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=NOW if status is WatchStatus.ACTIVE else None,
    )


def test_a_legacy_found_poll_records_the_found_watch() -> None:
    history = RecordingHistory()
    adapter = MockBookingAdapter()
    repository = InMemoryWatchRepository()
    _run(repository.save(_legacy_watch()))
    service, _ = build_service(adapter=adapter, repository=repository, history=history)

    result = _run(service.poll_once("watch_legacy"))

    assert result.outcome is WatchPollOutcome.FOUND
    assert history.calls == [("watch_legacy", WatchStatus.FOUND, None)]


def test_a_legacy_no_availability_poll_records_the_rescheduled_watch() -> None:
    history = RecordingHistory()
    repository = InMemoryWatchRepository()
    _run(repository.save(_legacy_watch()))
    service, _ = build_service(repository=repository, history=history)

    result = _run(service.poll_once("watch_legacy"))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert history.calls == [("watch_legacy", WatchStatus.ACTIVE, None)]
