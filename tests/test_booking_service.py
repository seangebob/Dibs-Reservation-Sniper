import asyncio
from datetime import UTC, datetime

from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import BookingStatus, ExecutionStatus
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationIntent,
    TimeWindow,
    VenueType,
)
from backend.services.booking_service import BookingService


def ready_intent(
    *,
    action: IntentAction = IntentAction.BOOK_RESERVATION,
    route: OrchestratorRoute = OrchestratorRoute.BOOKING_SERVICE,
    venue_name: str = "Cote",
) -> ReservationIntent:
    return ReservationIntent(
        status=IntentStatus.READY,
        route=route,
        action=action,
        venue_name=venue_name,
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-08-22",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[],
        clarification_question=None,
    )


def test_search_action_returns_slots_without_booking() -> None:
    service = BookingService(MockBookingAdapter())

    result = asyncio.run(
        service.execute(ready_intent(action=IntentAction.SEARCH_AVAILABILITY))
    )

    assert result.status is ExecutionStatus.AVAILABILITY_FOUND
    assert len(result.slots) == 1
    assert result.booking is None


def test_book_action_returns_idempotent_mock_confirmation() -> None:
    service = BookingService(
        MockBookingAdapter(
            clock=lambda: datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
        )
    )
    intent = ready_intent()

    first = asyncio.run(service.execute(intent))
    repeated = asyncio.run(service.execute(intent))

    assert first.status is ExecutionStatus.MOCK_BOOKED
    assert first.booking is not None
    assert repeated.booking == first.booking
    assert "existing mock reservation" in repeated.message


def test_no_availability_does_not_create_confirmation() -> None:
    service = BookingService(MockBookingAdapter(unavailable_venues={"Cote"}))

    result = asyncio.run(service.execute(ready_intent()))

    assert result.status is ExecutionStatus.NO_AVAILABILITY
    assert result.booking is None
    assert result.slots == []


def test_watch_action_stops_before_unimplemented_queue_layer() -> None:
    service = BookingService(MockBookingAdapter())

    result = asyncio.run(
        service.execute(
            ready_intent(
                action=IntentAction.CREATE_WATCH,
                route=OrchestratorRoute.WATCH_SERVICE,
            )
        )
    )

    assert result.status is ExecutionStatus.WATCH_REQUIRED
    assert result.booking is None
    assert "Milestone 3" in result.message


def test_incomplete_intent_never_calls_booking_adapter() -> None:
    intent = ReservationIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        route=OrchestratorRoute.CLARIFICATION,
        action=IntentAction.BOOK_RESERVATION,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=None,
        date="2026-08-22",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[MissingField.PARTY_SIZE],
        clarification_question="How many guests or participants are there?",
    )
    service = BookingService(MockBookingAdapter())

    result = asyncio.run(service.execute(intent))

    assert result.status is ExecutionStatus.CLARIFICATION_REQUIRED
    assert result.message == "How many guests or participants are there?"


def test_recreation_window_is_preserved_through_service() -> None:
    intent = ReservationIntent(
        status=IntentStatus.READY,
        route=OrchestratorRoute.BOOKING_SERVICE,
        action=IntentAction.SEARCH_AVAILABILITY,
        venue_name="Grand River Rocks",
        venue_type=VenueType.RECREATION,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=2,
        date="2026-08-22",
        preferred_time=None,
        time_window=TimeWindow(start="18:00", end="19:00"),
        duration_minutes=60,
        special_requests=["shoe rentals"],
        missing_fields=[],
        clarification_question=None,
    )

    result = asyncio.run(BookingService(MockBookingAdapter()).execute(intent))

    assert [slot.start_time for slot in result.slots] == [
        "18:00",
        "18:15",
        "18:30",
        "18:45",
        "19:00",
    ]
    assert all(slot.end_time is not None for slot in result.slots)


def test_retry_survives_service_recreation_and_semantic_variation() -> None:
    adapter = MockBookingAdapter()
    original = ready_intent().model_copy(
        update={"special_requests": ["Quiet table", "Window seat"]}
    )
    equivalent = ready_intent().model_copy(
        update={
            "venue_name": "cote",
            "special_requests": ["window seat", "quiet table"],
        }
    )

    first = asyncio.run(BookingService(adapter).execute(original))
    retried = asyncio.run(BookingService(adapter).execute(equivalent))

    assert first.booking is not None
    assert retried.status is ExecutionStatus.MOCK_BOOKED
    assert retried.booking == first.booking
    assert "existing mock reservation" in retried.message


class SlowSearchAdapter(MockBookingAdapter):
    """Yields between search and booking so requests genuinely interleave."""

    async def search_availability(self, query):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return await super().search_availability(query)


def test_simultaneous_bookings_of_one_slot_leave_a_single_winner() -> None:
    adapter = SlowSearchAdapter()
    service = BookingService(adapter)

    async def race() -> list[object]:
        return await asyncio.gather(
            service.execute(ready_intent().model_copy(update={"special_requests": ["booth"]})),
            service.execute(ready_intent().model_copy(update={"special_requests": ["patio"]})),
            return_exceptions=True,
        )

    first, second = asyncio.run(race())

    assert not isinstance(first, BaseException)
    assert not isinstance(second, BaseException)
    outcomes = sorted(result.status.value for result in (first, second))
    assert outcomes == ["MOCK_BOOKED", "NO_AVAILABILITY"]

    loser = first if first.status is ExecutionStatus.NO_AVAILABILITY else second
    assert loser.booking is None
    assert loser.slots == []


def test_concurrent_identical_requests_share_one_booking() -> None:
    service = BookingService(SlowSearchAdapter())
    intent = ready_intent()

    async def race() -> list[object]:
        return await asyncio.gather(*(service.execute(intent) for _ in range(5)))

    results = asyncio.run(race())

    booking_ids = {result.booking.booking_id for result in results}
    assert all(result.status is ExecutionStatus.MOCK_BOOKED for result in results)
    assert len(booking_ids) == 1


def test_confirmation_carries_every_required_field() -> None:
    service = BookingService(MockBookingAdapter())

    result = asyncio.run(service.execute(ready_intent()))

    assert result.booking is not None
    booking = result.booking.model_dump()
    assert booking["booking_id"]
    assert booking["status"] is BookingStatus.MOCK_CONFIRMED
    assert booking["provider"] == "mock"
    assert booking["created_at"] is not None
    slot = booking["slot"]
    assert slot["venue_name"] == "Cote"
    assert slot["date"] == "2026-08-22"
    assert slot["start_time"] == "19:00"
    assert slot["party_size"] == 4


def test_only_mock_confirmed_status_exists() -> None:
    assert [status.value for status in BookingStatus] == ["MOCK_CONFIRMED"]


def test_closed_holiday_reports_no_availability_not_empty_success() -> None:
    service = BookingService(MockBookingAdapter())

    result = asyncio.run(
        service.execute(
            ready_intent().model_copy(update={"date": "2026-12-25"}).model_copy(
                update={"action": IntentAction.SEARCH_AVAILABILITY}
            )
        )
    )

    assert result.status is ExecutionStatus.NO_AVAILABILITY
    assert result.slots == []
    assert result.booking is None


def test_repeated_identical_prompt_returns_the_same_slot() -> None:
    adapter = MockBookingAdapter()
    service = BookingService(adapter)
    intent = ready_intent()

    runs = [asyncio.run(service.execute(intent)) for _ in range(3)]

    assert {run.booking.booking_id for run in runs} == {runs[0].booking.booking_id}
    assert {run.booking.slot.start_time for run in runs} == {"19:00"}


def test_venue_casing_alone_does_not_create_a_second_booking() -> None:
    adapter = MockBookingAdapter()

    first = asyncio.run(BookingService(adapter).execute(ready_intent(venue_name="Cote")))
    again = asyncio.run(BookingService(adapter).execute(ready_intent(venue_name="COTE")))

    assert again.booking == first.booking
