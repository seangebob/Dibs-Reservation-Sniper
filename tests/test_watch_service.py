"""The queue handler: one poll, its state transition, and its successor job."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.base import AdapterError
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import WatchPollOutcome, WatchStatus
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationIntent,
    VenueType,
)
from backend.services.notification_service import (
    RecordingNotificationService,
    WatchEvent,
)
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue
from backend.workers.scheduler import PollSchedule


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
TARGET_DATE = "2026-09-05"


def query(venue_name: str = "Cote", party_size: int = 4) -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name=venue_name,
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=party_size,
        date=TARGET_DATE,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def watch_intent() -> ReservationIntent:
    return ReservationIntent(
        status=IntentStatus.READY,
        route=OrchestratorRoute.WATCH_SERVICE,
        action=IntentAction.CREATE_WATCH,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date=TARGET_DATE,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[],
        clarification_question=None,
    )


class EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a watch keeps polling."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


class FailingAdapter(MockBookingAdapter):
    """Raises the way a provider outage would."""

    async def search_availability(self, query):  # noqa: ANN001
        raise AdapterError("provider is down")


def build_service(
    adapter=None,
    *,
    repository=None,
    queue=None,
    notifier=None,
    max_attempts: int = 10,
    clock=None,
) -> tuple[WatchService, InMemoryWatchRepository, RecordingTaskQueue, RecordingNotificationService]:
    repository = repository or InMemoryWatchRepository()
    queue = queue or RecordingTaskQueue()
    notifier = notifier or RecordingNotificationService()
    service = WatchService(
        repository,
        adapter or EmptyAdapter(),
        queue,
        schedule=PollSchedule(interval_seconds=180, jitter_seconds=30),
        notifier=notifier,
        max_attempts=max_attempts,
        clock=clock or (lambda: NOW),
    )
    return service, repository, queue, notifier


# --- creation ---------------------------------------------------------------


def test_creating_a_watch_persists_it_and_dispatches_the_first_check() -> None:
    service, repository, queue, _ = build_service()

    watch = asyncio.run(service.create(query()))

    assert watch.status is WatchStatus.ACTIVE
    assert asyncio.run(repository.get(watch.watch_id)) == watch
    # The first check is immediate: the user just asked for it.
    assert queue.dispatches == [(watch.watch_id, 0.0)]


def test_watch_expires_at_the_end_of_the_reservation_day() -> None:
    service, _, _, _ = build_service()

    watch = asyncio.run(service.create(query()))

    assert watch.expires_at == datetime(2026, 9, 6, tzinfo=UTC)


def test_create_from_intent_carries_the_reservation_parameters() -> None:
    service, _, _, _ = build_service()

    watch = asyncio.run(service.create_from_intent(watch_intent()))

    assert watch.query.venue_name == "Cote"
    assert watch.query.party_size == 4
    assert watch.query.date == TARGET_DATE


def test_incomplete_intent_cannot_open_a_watch() -> None:
    service, _, _, _ = build_service()
    incomplete = ReservationIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        route=OrchestratorRoute.CLARIFICATION,
        action=IntentAction.CREATE_WATCH,
        venue_name=None,
        venue_type=VenueType.UNKNOWN,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=None,
        date=None,
        preferred_time=None,
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[MissingField.VENUE_NAME],
        clarification_question="Which venue?",
    )

    with pytest.raises(ValueError, match="clarification"):
        asyncio.run(service.create_from_intent(incomplete))


# --- polling ----------------------------------------------------------------


def test_poll_without_availability_reschedules_with_jitter() -> None:
    service, repository, queue, _ = build_service()
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert 150.0 <= result.retry_in_seconds <= 210.0
    assert queue.dispatches == [(watch.watch_id, result.retry_in_seconds)]

    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.ACTIVE
    assert stored.attempts == 1
    assert stored.last_checked_at == NOW
    assert stored.next_check_at == NOW + timedelta(seconds=result.retry_in_seconds)


def test_each_poll_schedules_exactly_one_successor() -> None:
    """A watch must never fan out into concurrent polling chains."""

    service, _, queue, _ = build_service()
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    for _ in range(5):
        asyncio.run(service.poll_once(watch.watch_id))

    assert len(queue.dispatches) == 5
    assert {watch_id for watch_id, _ in queue.dispatches} == {watch.watch_id}


def test_finding_availability_finishes_the_watch_and_notifies() -> None:
    service, repository, queue, notifier = build_service(MockBookingAdapter())
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert result.retry_in_seconds is None
    assert queue.dispatches == []

    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.FOUND
    assert stored.found_slots
    assert stored.next_check_at is None
    assert notifier.events == [(watch.watch_id, WatchEvent.AVAILABILITY_FOUND)]


def test_auto_book_watch_books_the_slot_it_finds() -> None:
    service, repository, _, notifier = build_service(MockBookingAdapter())
    watch = asyncio.run(service.create(query(), auto_book=True))

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.BOOKED
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.BOOKED
    assert stored.booking is not None
    assert stored.booking.slot.venue_name == "Cote"
    assert notifier.events == [(watch.watch_id, WatchEvent.BOOKED)]


def test_replayed_auto_book_returns_the_same_reservation() -> None:
    """A broker redelivery must not produce a second reservation."""

    adapter = MockBookingAdapter()
    service, repository, _, _ = build_service(adapter)
    watch = asyncio.run(service.create(query(), auto_book=True))
    asyncio.run(service.poll_once(watch.watch_id))
    first = asyncio.run(repository.get(watch.watch_id)).booking

    # Force the watch back to ACTIVE, as a duplicate delivery would find it.
    asyncio.run(
        repository.save(
            (asyncio.run(repository.get(watch.watch_id))).model_copy(
                update={"status": WatchStatus.ACTIVE, "booking": None}
            )
        )
    )
    asyncio.run(service.poll_once(watch.watch_id))
    second = asyncio.run(repository.get(watch.watch_id)).booking

    assert second.booking_id == first.booking_id


# --- stopping conditions ----------------------------------------------------


def test_cancelled_watch_stops_the_chain_on_the_next_poll() -> None:
    service, _, queue, _ = build_service()
    watch = asyncio.run(service.create(query()))
    asyncio.run(service.cancel(watch.watch_id))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.ALREADY_FINISHED
    assert queue.dispatches == []


def test_cancelling_clears_the_scheduled_check() -> None:
    service, repository, _, _ = build_service()
    watch = asyncio.run(service.create(query()))

    cancelled = asyncio.run(service.cancel(watch.watch_id))

    assert cancelled.status is WatchStatus.CANCELLED
    assert cancelled.next_check_at is None
    assert asyncio.run(repository.get(watch.watch_id)).status is WatchStatus.CANCELLED


def test_cancelling_a_finished_watch_leaves_it_alone() -> None:
    service, _, _, _ = build_service(MockBookingAdapter())
    watch = asyncio.run(service.create(query()))
    asyncio.run(service.poll_once(watch.watch_id))

    cancelled = asyncio.run(service.cancel(watch.watch_id))

    assert cancelled.status is WatchStatus.FOUND


def test_cancelling_an_unknown_watch_returns_none() -> None:
    service, _, _, _ = build_service()

    assert asyncio.run(service.cancel("watch_missing")) is None


def test_exhausting_attempts_expires_the_watch() -> None:
    service, repository, queue, notifier = build_service(max_attempts=3)
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    outcomes = [
        asyncio.run(service.poll_once(watch.watch_id)).outcome for _ in range(3)
    ]

    assert outcomes == [
        WatchPollOutcome.NO_AVAILABILITY,
        WatchPollOutcome.NO_AVAILABILITY,
        WatchPollOutcome.EXPIRED,
    ]
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.EXPIRED
    assert stored.next_check_at is None
    # Two retries queued, then nothing more.
    assert len(queue.dispatches) == 2
    assert notifier.events == [(watch.watch_id, WatchEvent.EXPIRED)]


def test_watch_expires_once_the_reservation_date_has_passed() -> None:
    clock = iter([NOW, datetime(2026, 9, 7, 12, 0, tzinfo=UTC)])
    service, repository, queue, _ = build_service(clock=lambda: next(clock))
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.EXPIRED
    assert queue.dispatches == []
    assert asyncio.run(repository.get(watch.watch_id)).status is WatchStatus.EXPIRED


def test_polling_an_unknown_watch_is_not_an_error() -> None:
    service, _, queue, _ = build_service()

    result = asyncio.run(service.poll_once("watch_missing"))

    assert result.outcome is WatchPollOutcome.UNKNOWN_WATCH
    assert result.watch is None
    assert queue.dispatches == []


# --- provider failures ------------------------------------------------------


def test_provider_outage_keeps_the_watch_alive_and_records_the_error() -> None:
    service, repository, queue, _ = build_service(FailingAdapter())
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert len(queue.dispatches) == 1
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.ACTIVE
    assert stored.last_error == "provider is down"


def test_recovered_provider_clears_the_recorded_error() -> None:
    service, repository, _, _ = build_service(FailingAdapter())
    watch = asyncio.run(service.create(query()))
    asyncio.run(service.poll_once(watch.watch_id))

    service._adapter = EmptyAdapter()
    asyncio.run(service.poll_once(watch.watch_id))

    assert asyncio.run(repository.get(watch.watch_id)).last_error is None


# --- listing ----------------------------------------------------------------


def test_listing_can_be_narrowed_to_active_watches() -> None:
    service, _, _, _ = build_service()
    first = asyncio.run(service.create(query("Cote")))
    asyncio.run(service.create(query("Bhima's Warung")))
    asyncio.run(service.cancel(first.watch_id))

    assert len(asyncio.run(service.list())) == 2
    assert len(asyncio.run(service.list(active_only=True))) == 1
