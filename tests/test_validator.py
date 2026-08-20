from datetime import datetime
from zoneinfo import ZoneInfo

from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationExtraction,
    TimeWindow,
    VenueType,
)
from backend.orchestrator.validator import IntentValidator


REFERENCE_TIME = datetime(
    2026,
    8,
    18,
    12,
    30,
    tzinfo=ZoneInfo("America/Toronto"),
)


def extraction(**updates: object) -> ReservationExtraction:
    data: dict[str, object] = {
        "action": IntentAction.BOOK_RESERVATION,
        "venue_name": "Cote",
        "venue_type": VenueType.RESTAURANT,
        "party_size": 4,
        "date": "2026-08-22",
        "preferred_time": "19:00",
        "time_window": None,
        "duration_minutes": None,
        "special_requests": [],
    }
    data.update(updates)
    return ReservationExtraction.model_validate(data)


def test_ready_booking_routes_to_booking_service() -> None:
    result = IntentValidator().validate(extraction(), REFERENCE_TIME)

    assert result.status is IntentStatus.READY
    assert result.route is OrchestratorRoute.BOOKING_SERVICE
    assert result.market == "Kitchener-Waterloo, ON"
    assert result.is_ready is True
    assert result.missing_fields == []


def test_recreation_watch_with_time_window_routes_to_watch_service() -> None:
    result = IntentValidator().validate(
        extraction(
            action=IntentAction.CREATE_WATCH,
            venue_name="Grand River Rocks",
            venue_type=VenueType.RECREATION,
            preferred_time=None,
            time_window={"start": "18:00", "end": "21:00"},
            duration_minutes=120,
            special_requests=["shoe rentals"],
        ),
        REFERENCE_TIME,
    )

    assert result.status is IntentStatus.READY
    assert result.route is OrchestratorRoute.WATCH_SERVICE
    assert result.venue_type is VenueType.RECREATION
    assert result.time_window == TimeWindow(start="18:00", end="21:00")


def test_missing_fields_route_to_one_clarification_question() -> None:
    result = IntentValidator().validate(
        extraction(venue_name=None, party_size=None, preferred_time=None),
        REFERENCE_TIME,
    )

    assert result.status is IntentStatus.NEEDS_CLARIFICATION
    assert result.route is OrchestratorRoute.CLARIFICATION
    assert result.missing_fields == [
        MissingField.VENUE_NAME,
        MissingField.PARTY_SIZE,
        MissingField.TIME,
    ]
    assert result.clarification_question == (
        "Could you provide the venue, party size, and time or time range?"
    )


def test_past_date_cannot_be_routed_for_execution() -> None:
    result = IntentValidator().validate(
        extraction(date="2026-08-17"),
        REFERENCE_TIME,
    )

    assert result.is_ready is False
    assert result.date is None
    assert result.missing_fields == [MissingField.DATE]
    assert result.clarification_question == (
        "That date has already passed. What future date would you like?"
    )


def test_backwards_time_window_requires_clarification() -> None:
    result = IntentValidator().validate(
        extraction(
            preferred_time=None,
            time_window={"start": "21:00", "end": "18:00"},
        ),
        REFERENCE_TIME,
    )

    assert result.time_window is None
    assert result.missing_fields == [MissingField.TIME]
    assert result.clarification_question == (
        "The time window is invalid. What time or valid time range works for you?"
    )


def test_preferred_time_outside_window_requires_clarification() -> None:
    result = IntentValidator().validate(
        extraction(
            preferred_time="23:00",
            time_window={"start": "18:00", "end": "19:00"},
        ),
        REFERENCE_TIME,
    )

    assert result.status is IntentStatus.NEEDS_CLARIFICATION
    assert result.route is OrchestratorRoute.CLARIFICATION
    assert result.missing_fields == [MissingField.TIME]
    assert result.clarification_question == (
        "The preferred time falls outside the requested time window. "
        "Which time constraint should I use?"
    )
