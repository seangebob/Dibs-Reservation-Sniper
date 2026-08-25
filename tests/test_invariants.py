"""Safety rails that must hold whatever the language model returns."""

import asyncio
from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from backend.integrations.base import SlotUnavailableError
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import (
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
    ExecutionStatus,
    PromptExecutionResult,
)
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


def intent_data(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "status": IntentStatus.READY,
        "route": OrchestratorRoute.BOOKING_SERVICE,
        "action": IntentAction.BOOK_RESERVATION,
        "venue_name": "Cote",
        "venue_type": VenueType.RESTAURANT,
        "market": "Kitchener-Waterloo, ON",
        "party_size": 4,
        "date": "2026-08-22",
        "preferred_time": "19:00",
        "time_window": None,
        "duration_minutes": None,
        "special_requests": [],
        "missing_fields": [],
        "clarification_question": None,
    }
    data.update(updates)
    return data


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"party_size": None}, "missing required reservation data"),
        ({"venue_name": None}, "missing required reservation data"),
        ({"date": None}, "missing required reservation data"),
        ({"action": None}, "missing required reservation data"),
        (
            {"preferred_time": None, "time_window": None},
            "missing required reservation data",
        ),
        (
            {"route": OrchestratorRoute.CLARIFICATION},
            "cannot route to clarification",
        ),
        (
            {"missing_fields": [MissingField.DATE]},
            "cannot contain clarification data",
        ),
        (
            {"clarification_question": "Which date?"},
            "cannot contain clarification data",
        ),
    ],
)
def test_ready_intent_cannot_hide_missing_data(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ReservationIntent.model_validate(intent_data(**updates))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"route": OrchestratorRoute.BOOKING_SERVICE},
            "must route to clarification",
        ),
        (
            {
                "route": OrchestratorRoute.CLARIFICATION,
                "missing_fields": [],
                "clarification_question": "Which date?",
            },
            "requires missing fields and a question",
        ),
        (
            {
                "route": OrchestratorRoute.CLARIFICATION,
                "missing_fields": [MissingField.DATE],
                "clarification_question": None,
            },
            "requires missing fields and a question",
        ),
    ],
)
def test_incomplete_intent_must_ask_a_question(
    updates: dict[str, object],
    message: str,
) -> None:
    data = intent_data(
        status=IntentStatus.NEEDS_CLARIFICATION,
        route=OrchestratorRoute.CLARIFICATION,
        missing_fields=[MissingField.DATE],
        clarification_question="What date would you like?",
        date=None,
    )
    data.update(updates)

    with pytest.raises(ValidationError, match=message):
        ReservationIntent.model_validate(data)


def test_intent_rejects_an_impossible_calendar_date() -> None:
    with pytest.raises(ValidationError):
        ReservationIntent.model_validate(intent_data(date="2026-02-30"))


def slot(**updates: object) -> AvailabilitySlot:
    data: dict[str, object] = {
        "slot_id": "mock_1",
        "provider": "mock",
        "venue_name": "Cote",
        "venue_type": VenueType.RESTAURANT,
        "date": "2026-08-22",
        "start_time": "19:00",
        "end_time": None,
        "party_size": 4,
        "max_party_size": 6,
    }
    data.update(updates)
    return AvailabilitySlot.model_validate(data)


def test_slot_cannot_seat_more_than_its_table_holds() -> None:
    with pytest.raises(ValidationError, match="cannot seat"):
        slot(party_size=8, max_party_size=6)


def test_booked_result_must_carry_a_confirmation() -> None:
    with pytest.raises(ValidationError, match="requires a confirmation"):
        PromptExecutionResult(
            status=ExecutionStatus.MOCK_BOOKED,
            intent=ReservationIntent.model_validate(intent_data()),
            slots=[slot()],
            booking=None,
            message="done",
        )


def test_unbooked_result_must_not_carry_a_confirmation() -> None:
    confirmation = BookingConfirmation(
        booking_id="mock_booking_1",
        provider="mock",
        status=BookingStatus.MOCK_CONFIRMED,
        slot=slot(),
        created_at=datetime(2026, 8, 18, 16, 0, tzinfo=UTC),
    )

    with pytest.raises(ValidationError, match="only MOCK_BOOKED"):
        PromptExecutionResult(
            status=ExecutionStatus.AVAILABILITY_FOUND,
            intent=ReservationIntent.model_validate(intent_data()),
            slots=[slot()],
            booking=confirmation,
            message="found",
        )


def test_clarification_result_requires_an_incomplete_intent() -> None:
    with pytest.raises(ValidationError, match="requires an incomplete intent"):
        PromptExecutionResult(
            status=ExecutionStatus.CLARIFICATION_REQUIRED,
            intent=ReservationIntent.model_validate(intent_data()),
            slots=[],
            booking=None,
            message="Which date?",
        )


def test_availability_query_needs_some_time_preference() -> None:
    from backend.models.reservation import AvailabilityQuery

    with pytest.raises(ValidationError, match="requires a time or time window"):
        AvailabilityQuery(
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo, ON",
            party_size=4,
            date="2026-08-22",
            preferred_time=None,
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        )


class FlakyAdapter(MockBookingAdapter):
    """Reports the first offered slot as taken, as a real platform might."""

    def __init__(self) -> None:
        super().__init__()
        self.rejected: list[str] = []

    async def book_slot(self, slot_id: str, *, idempotency_key: str):  # type: ignore[no-untyped-def]
        if not self.rejected:
            self.rejected.append(slot_id)
            raise SlotUnavailableError(f"taken: {slot_id}")
        return await super().book_slot(slot_id, idempotency_key=idempotency_key)


def test_a_slot_lost_mid_booking_falls_through_to_the_next_one() -> None:
    adapter = FlakyAdapter()
    intent = ReservationIntent.model_validate(
        intent_data(preferred_time=None, time_window=TimeWindow(start="18:00", end="20:00"))
    )

    result = asyncio.run(BookingService(adapter).execute(intent))

    assert result.status is ExecutionStatus.MOCK_BOOKED
    assert result.booking is not None
    assert result.booking.slot.slot_id != adapter.rejected[0]


class AlwaysTakenAdapter(MockBookingAdapter):
    async def book_slot(self, slot_id: str, *, idempotency_key: str):  # type: ignore[no-untyped-def]
        raise SlotUnavailableError(f"taken: {slot_id}")


def test_losing_every_slot_reports_no_availability_not_an_error() -> None:
    intent = ReservationIntent.model_validate(
        intent_data(preferred_time=None, time_window=TimeWindow(start="18:00", end="20:00"))
    )

    result = asyncio.run(BookingService(AlwaysTakenAdapter()).execute(intent))

    assert result.status is ExecutionStatus.NO_AVAILABILITY
    assert result.booking is None
    assert "taken before the booking" in result.message
