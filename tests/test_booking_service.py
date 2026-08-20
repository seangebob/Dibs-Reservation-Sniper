import asyncio
from datetime import UTC, datetime

from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import ExecutionStatus
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
        market="Kitchener-Waterloo, ON",
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
        market="Kitchener-Waterloo, ON",
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
        market="Kitchener-Waterloo, ON",
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

    assert [slot.start_time for slot in result.slots] == ["18:00", "18:30", "19:00"]
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
