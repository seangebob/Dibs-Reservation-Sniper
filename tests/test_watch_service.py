"""The queue handler: one poll, its state transition, and its successor job."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.base import AdapterError, SlotUnavailableError
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
from backend.models.watch import Watch
from backend.models.watch_runtime import initial_runtime, window_id_for
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


def test_a_redelivery_after_an_external_booking_replays_it() -> None:
    """A crash after booking but before commit must not double-book."""

    clock = Clock()
    adapter = MockBookingAdapter()
    service, repository, _ = build_shared(adapter, clock)
    watch = asyncio.run(service.create(query(), auto_book=True))

    # The reservation lands at the provider under the stable key, then the
    # worker crashes before the watch can commit. The redelivery must find and
    # adopt that same reservation rather than book a second one.
    key = f"watch:{watch.watch_id}"
    slots = asyncio.run(adapter.search_availability(watch.query))
    external = asyncio.run(
        adapter.book_slot(slots[0].slot_id, idempotency_key=key)
    )

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.BOOKED
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.BOOKED
    assert stored.booking.booking_id == external.booking_id


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


# --- claim-first cadence windows (milestone 3, single-flight) ---------------


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class CountingAdapter(EmptyAdapter):
    """Never has availability, and counts how often it is searched."""

    def __init__(self) -> None:
        self.searches = 0

    async def search_availability(self, query):  # noqa: ANN001
        self.searches += 1
        return []


def build_shared(  # noqa: ANN201
    adapter,  # noqa: ANN001
    clock,  # noqa: ANN001
    *,
    max_attempts: int = 10,
    provider_timeout_seconds: float = 45.0,
    backoff_max_seconds: float = 3600.0,
    notifier=None,  # noqa: ANN001
):
    """A service and repository sharing one injected clock, for due-time tests."""

    repository = InMemoryWatchRepository(clock=clock)
    queue = RecordingTaskQueue()
    service = WatchService(
        repository,
        adapter,
        queue,
        schedule=PollSchedule(interval_seconds=180, jitter_seconds=30),
        notifier=notifier or RecordingNotificationService(),
        max_attempts=max_attempts,
        provider_timeout_seconds=provider_timeout_seconds,
        backoff_max_seconds=backoff_max_seconds,
        clock=clock,
    )
    return service, repository, queue


def test_a_duplicate_delivery_of_a_window_does_no_second_check() -> None:
    """A redelivery of a window already advanced past is a no-op."""

    clock = Clock()
    adapter = CountingAdapter()
    service, repository, _ = build_shared(adapter, clock)
    watch = asyncio.run(service.create(query()))
    window = window_id_for(watch.watch_id, 0)

    first = asyncio.run(service.poll_window(watch.watch_id, window, enforce_due=False))
    second = asyncio.run(service.poll_window(watch.watch_id, window, enforce_due=False))

    assert first.outcome is WatchPollOutcome.NO_AVAILABILITY
    # The window moved on after the first commit, so the redelivery finds it
    # stale: no provider call, no second attempt, no extra successor.
    assert second.outcome is WatchPollOutcome.ALREADY_FINISHED
    assert adapter.searches == 1
    assert asyncio.run(repository.get(watch.watch_id)).attempts == 1


def test_a_window_polled_before_it_is_due_does_no_work() -> None:
    """The window-aware path leaves a not-yet-due window untouched."""

    clock = Clock()
    adapter = CountingAdapter()
    service, _, _ = build_shared(adapter, clock)
    watch = asyncio.run(service.create(query()))
    # The first miss schedules the next window in the future (clock is frozen).
    asyncio.run(service.poll_once(watch.watch_id))
    searches_after_first = adapter.searches
    next_window = window_id_for(watch.watch_id, 1)

    result = asyncio.run(
        service.poll_window(watch.watch_id, next_window, enforce_due=True)
    )

    assert result.outcome is WatchPollOutcome.ALREADY_FINISHED
    assert adapter.searches == searches_after_first  # no premature provider call


def test_a_near_deadline_miss_never_schedules_past_the_deadline() -> None:
    clock = Clock()
    adapter = CountingAdapter()
    service, repository, _ = build_shared(adapter, clock)
    # Seed a watch with only ten seconds of lifetime left.
    watch = Watch(
        watch_id="watch_short",
        status=WatchStatus.ACTIVE,
        query=query(),
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
        attempts=0,
        max_attempts=25_000,
        next_check_at=NOW,
    )
    runtime = initial_runtime(watch, required_attempts=2, supports_deadline=True)
    asyncio.run(repository.create_with_schedule(watch, runtime))

    result = asyncio.run(
        service.poll_window(
            watch.watch_id, window_id_for(watch.watch_id, 0), enforce_due=False
        )
    )

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert result.retry_in_seconds <= 10
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.next_check_at <= stored.expires_at


# --- provider outage backoff (milestone 3, no-attempt capped exponential) ----


class SleepingAdapter(EmptyAdapter):
    """Takes longer than the provider-sequence deadline allows."""

    async def search_availability(self, query):  # noqa: ANN001
        await asyncio.sleep(0.05)
        return []


def _seed_active(repository, clock, *, expires_at) -> "Watch":  # noqa: ANN001
    watch = Watch(
        watch_id="watch_outage",
        status=WatchStatus.ACTIVE,
        query=query(),
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at,
        attempts=0,
        max_attempts=25_000,
        next_check_at=NOW,
    )
    runtime = initial_runtime(watch, required_attempts=2, supports_deadline=True)
    asyncio.run(repository.create_with_schedule(watch, runtime))
    return watch


def test_an_outage_consumes_no_availability_attempt() -> None:
    clock = Clock()
    service, repository, queue = build_shared(FailingAdapter(), clock)
    watch = asyncio.run(service.create(query()))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.ACTIVE
    assert stored.attempts == 0  # the outage did not spend the user's budget
    assert stored.last_error == "provider is down"
    runtime = asyncio.run(repository.get_runtime(watch.watch_id))
    assert runtime.consecutive_outages == 1
    assert len(queue.dispatches) == 1


def test_consecutive_outages_back_off_exponentially() -> None:
    clock = Clock()
    service, repository, _ = build_shared(FailingAdapter(), clock)
    watch = asyncio.run(service.create(query()))

    first = asyncio.run(service.poll_once(watch.watch_id))
    second = asyncio.run(service.poll_once(watch.watch_id))

    # n=1 stays at the ordinary cadence; n=2 doubles the base interval. The
    # ranges do not overlap, so the escalation is unambiguous.
    assert 150.0 <= first.retry_in_seconds <= 210.0
    assert 330.0 <= second.retry_in_seconds <= 390.0
    assert asyncio.run(repository.get(watch.watch_id)).attempts == 0


def test_a_successful_check_after_an_outage_resets_the_backoff() -> None:
    clock = Clock()
    service, repository, _ = build_shared(FailingAdapter(), clock)
    watch = asyncio.run(service.create(query()))
    asyncio.run(service.poll_once(watch.watch_id))  # outage, n=1

    service._adapter = EmptyAdapter()
    recovered = asyncio.run(service.poll_once(watch.watch_id))

    assert recovered.outcome is WatchPollOutcome.NO_AVAILABILITY
    assert 150.0 <= recovered.retry_in_seconds <= 210.0  # back to normal cadence
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.attempts == 1  # the successful empty check spends one attempt
    runtime = asyncio.run(repository.get_runtime(watch.watch_id))
    assert runtime.consecutive_outages == 0


def test_a_provider_sequence_timeout_is_an_outage_not_an_attempt() -> None:
    clock = Clock()
    service, repository, _ = build_shared(
        SleepingAdapter(), clock, provider_timeout_seconds=0.01
    )
    watch = asyncio.run(service.create(query()))

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.attempts == 0
    assert "timed out" in stored.last_error
    assert asyncio.run(repository.get_runtime(watch.watch_id)).consecutive_outages == 1


def test_an_outage_with_less_than_the_floor_left_expires_without_rescheduling() -> None:
    clock = Clock()
    service, repository, queue = build_shared(FailingAdapter(), clock)
    watch = _seed_active(
        repository, clock, expires_at=NOW + timedelta(seconds=0.5)
    )

    result = asyncio.run(
        service.poll_window(
            watch.watch_id, window_id_for(watch.watch_id, 0), enforce_due=False
        )
    )

    assert result.outcome is WatchPollOutcome.EXPIRED
    assert queue.dispatches == []
    assert asyncio.run(repository.get(watch.watch_id)).status is WatchStatus.EXPIRED


def test_an_atomic_commit_finishes_even_if_the_poll_task_is_cancelled() -> None:
    """A Celery time limit must not leave a watch half-transitioned."""

    from backend.db.repositories.watch_decisions import CommitResult, CommitStatus

    committed: list[str] = []

    class BlockingRepo:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def commit_window(self, claim, new_watch, new_runtime):  # noqa: ANN001
            self.started.set()
            await self.release.wait()
            committed.append(new_watch)
            return CommitResult(CommitStatus.COMMITTED, watch=new_watch)

    async def scenario() -> None:
        repo = BlockingRepo()
        service = WatchService(repo, EmptyAdapter(), RecordingTaskQueue())
        task = asyncio.ensure_future(
            service._commit_window(claim=None, new_watch="sentinel", new_runtime=None)
        )
        await repo.started.wait()
        task.cancel()  # the outer poll is cancelled mid-commit
        repo.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The shielded commit still ran to completion before propagating.
        assert committed == ["sentinel"]

    asyncio.run(scenario())


# --- fenced auto-booking and cancellation races (milestone 3) ---------------


class _RacingBookAdapter(MockBookingAdapter):
    """Runs a callback in the moment between the booking permit and the book.

    That is exactly the window a cancellation must be able to lose or win in,
    so the callback lets a test record `cancel_requested` at that instant.
    """

    def __init__(self, on_book, *, then_fail=None) -> None:  # noqa: ANN001
        super().__init__()
        self._on_book = on_book
        self._then_fail = then_fail

    async def book_slot(self, slot_id, *, idempotency_key):  # noqa: ANN001
        await self._on_book(idempotency_key)
        if self._then_fail is not None:
            raise self._then_fail
        return await super().book_slot(slot_id, idempotency_key=idempotency_key)


def _cancel_when_booking(repository, watch_id):  # noqa: ANN001, ANN202
    async def hook(_key: str) -> None:
        await repository.cancel_if_active(watch_id)

    return hook


def test_a_cancellation_that_loses_the_booking_race_returns_booked() -> None:
    """A cancellation after the reservation lands returns BOOKED, not a lie."""

    clock = Clock()
    box: dict = {}

    async def hook(_key: str) -> None:
        await box["repo"].cancel_if_active(box["id"])

    adapter = _RacingBookAdapter(hook)
    service, repository, _ = build_shared(adapter, clock)
    box["repo"] = repository
    watch = asyncio.run(service.create(query(), auto_book=True))
    box["id"] = watch.watch_id

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.BOOKED
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.BOOKED
    assert stored.booking is not None


def test_a_cancellation_wins_when_the_booking_definitively_fails() -> None:
    """If nothing was booked, a cancellation recorded mid-flight wins."""

    clock = Clock()
    box: dict = {}

    async def hook(_key: str) -> None:
        await box["repo"].cancel_if_active(box["id"])

    adapter = _RacingBookAdapter(hook, then_fail=SlotUnavailableError("gone"))
    notifier = RecordingNotificationService()
    service, repository, _ = build_shared(adapter, clock, notifier=notifier)
    box["repo"] = repository
    watch = asyncio.run(service.create(query(), auto_book=True))
    box["id"] = watch.watch_id

    result = asyncio.run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.ALREADY_FINISHED
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.CANCELLED
    assert stored.booking is None
    assert notifier.events == []  # cancellation earns no notification


class _AmbiguousBookAdapter(MockBookingAdapter):
    """The reservation lands, then the confirmation is lost to an error."""

    async def book_slot(self, slot_id, *, idempotency_key):  # noqa: ANN001
        await super().book_slot(slot_id, idempotency_key=idempotency_key)
        raise AdapterError("connection dropped after the reservation committed")


class _FailedBookAdapter(MockBookingAdapter):
    """The booking call fails before any reservation is created."""

    async def book_slot(self, slot_id, *, idempotency_key):  # noqa: ANN001
        raise AdapterError("provider unreachable during booking")


def test_an_ambiguous_booking_error_reconciles_to_booked() -> None:
    clock = Clock()
    service, repository, _ = build_shared(_AmbiguousBookAdapter(), clock)
    watch = asyncio.run(service.create(query(), auto_book=True))

    result = asyncio.run(service.poll_once(watch.watch_id))

    # Authoritative reconciliation finds the reservation the error hid.
    assert result.outcome is WatchPollOutcome.BOOKED
    assert asyncio.run(repository.get(watch.watch_id)).status is WatchStatus.BOOKED


def test_an_ambiguous_booking_error_that_did_not_book_is_an_outage() -> None:
    clock = Clock()
    service, repository, queue = build_shared(_FailedBookAdapter(), clock)
    watch = asyncio.run(service.create(query(), auto_book=True))
    queue.dispatches.clear()

    result = asyncio.run(service.poll_once(watch.watch_id))

    # Reconciliation confirms no reservation and no cancellation: a provider
    # outage, so no attempt is spent and the watch stays active for a retry.
    assert result.outcome is WatchPollOutcome.NO_AVAILABILITY
    stored = asyncio.run(repository.get(watch.watch_id))
    assert stored.status is WatchStatus.ACTIVE
    assert stored.attempts == 0
    assert len(queue.dispatches) == 1
