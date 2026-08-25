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


REFERENCE_TIME = datetime(2026, 8, 18, 12, 30,
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


def test_ambiguous_venue_asks_which_one_instead_of_guessing() -> None:
    result = IntentValidator().validate(extraction(venue_name="Kitchen"), REFERENCE_TIME)

    assert result.status is IntentStatus.NEEDS_CLARIFICATION
    assert result.route is OrchestratorRoute.CLARIFICATION
    assert result.missing_fields == [MissingField.VENUE_NAME]
    assert result.clarification_question == (
        "Did you mean The Bauer Kitchen or Proof Kitchen + Lounge?"
    )


def test_ambiguous_venue_question_lists_three_candidates_readably() -> None:
    result = IntentValidator().validate(
        extraction(venue_name="steak house"),
        REFERENCE_TIME,
    )

    assert "Charcoal" in result.clarification_question
    assert "Golf" in result.clarification_question
    assert len(result.clarification_question) <= 400


def test_known_venue_is_canonicalized_and_typed_from_the_catalog() -> None:
    result = IntentValidator().validate(
        extraction(venue_name="  grr ", venue_type=VenueType.UNKNOWN),
        REFERENCE_TIME,
    )

    assert result.is_ready is True
    assert result.venue_name == "Grand River Rocks"
    assert result.venue_type is VenueType.RECREATION


def test_unknown_venue_is_passed_through_untouched() -> None:
    result = IntentValidator().validate(extraction(venue_name="Cote"), REFERENCE_TIME)

    assert result.is_ready is True
    assert result.venue_name == "Cote"


def test_impossible_calendar_date_asks_for_a_real_one() -> None:
    result = IntentValidator().validate(extraction(date="2026-02-30"), REFERENCE_TIME)

    assert result.date is None
    assert result.missing_fields == [MissingField.DATE]
    assert result.clarification_question == (
        "That date does not exist on the calendar. What date would you like?"
    )


def test_date_more_than_a_year_out_is_refused() -> None:
    result = IntentValidator().validate(extraction(date="2099-01-01"), REFERENCE_TIME)

    assert result.date is None
    assert result.missing_fields == [MissingField.DATE]
    assert "up to a year ahead" in result.clarification_question


def test_date_exactly_a_year_out_is_still_accepted() -> None:
    result = IntentValidator().validate(extraction(date="2027-08-18"), REFERENCE_TIME)

    assert result.is_ready is True
    assert result.date == "2027-08-18"


def test_today_is_bookable_but_a_time_already_past_is_not() -> None:
    late = datetime(2026, 8, 18, 20, 30, tzinfo=ZoneInfo("America/Toronto"))

    upcoming = IntentValidator().validate(
        extraction(date="2026-08-18", preferred_time="21:00"),
        late,
    )
    elapsed = IntentValidator().validate(
        extraction(date="2026-08-18", preferred_time="19:00"),
        late,
    )

    assert upcoming.is_ready is True
    assert elapsed.missing_fields == [MissingField.TIME]
    assert elapsed.clarification_question == (
        "That time has already passed today. What later time works, "
        "or should I look at another day?"
    )


def test_window_starting_in_the_past_is_clamped_to_the_next_slot() -> None:
    result = IntentValidator().validate(
        extraction(
            date="2026-08-18",
            preferred_time=None,
            time_window={"start": "12:00", "end": "22:00"},
        ),
        datetime(2026, 8, 18, 18, 7, tzinfo=ZoneInfo("America/Toronto")),
    )

    assert result.is_ready is True
    assert result.time_window == TimeWindow(start="18:15", end="22:00")


def test_window_entirely_in_the_past_requires_another_time() -> None:
    result = IntentValidator().validate(
        extraction(
            date="2026-08-18",
            preferred_time=None,
            time_window={"start": "12:00", "end": "14:00"},
        ),
        datetime(2026, 8, 18, 18, 0, tzinfo=ZoneInfo("America/Toronto")),
    )

    assert result.time_window is None
    assert result.missing_fields == [MissingField.TIME]
    assert "already passed today" in result.clarification_question


def test_late_evening_does_not_roll_the_date_forward() -> None:
    """23:59 local must still treat today as today, not tomorrow."""

    result = IntentValidator().validate(
        extraction(date="2026-08-18", preferred_time="23:59"),
        datetime(2026, 8, 18, 23, 58, tzinfo=ZoneInfo("America/Toronto")),
    )

    assert result.is_ready is True
    assert result.date == "2026-08-18"


def test_just_after_midnight_does_not_reject_the_new_day() -> None:
    """00:05 local must not treat the current day as already past."""

    result = IntentValidator().validate(
        extraction(date="2026-08-19", preferred_time="19:00"),
        datetime(2026, 8, 19, 0, 5, tzinfo=ZoneInfo("America/Toronto")),
    )

    assert result.is_ready is True
    assert result.date == "2026-08-19"


def test_utc_instant_is_judged_in_kitchener_waterloo_local_time() -> None:
    """03:00 UTC on the 19th is still the evening of the 18th in KW."""

    utc_instant = datetime(2026, 8, 19, 3, 0, tzinfo=ZoneInfo("UTC"))
    local = utc_instant.astimezone(ZoneInfo("America/Toronto"))

    result = IntentValidator().validate(
        extraction(date="2026-08-18", preferred_time="23:30"),
        local,
    )

    assert local.date().isoformat() == "2026-08-18"
    assert result.is_ready is True
    assert result.date == "2026-08-18"


def test_absent_action_is_asked_about() -> None:
    result = IntentValidator().validate(extraction(action=None), REFERENCE_TIME)

    assert result.missing_fields == [MissingField.ACTION]
    assert result.clarification_question == (
        "Would you like me to check availability, book it, or create a watch?"
    )


def test_absent_date_asks_plainly() -> None:
    result = IntentValidator().validate(extraction(date=None), REFERENCE_TIME)

    assert result.date is None
    assert result.missing_fields == [MissingField.DATE]
    assert result.clarification_question == "What date would you like?"


def test_single_missing_party_size_asks_only_about_that() -> None:
    result = IntentValidator().validate(extraction(party_size=None), REFERENCE_TIME)

    assert result.clarification_question == (
        "How many guests or participants are there?"
    )


def test_two_missing_fields_are_joined_with_and() -> None:
    result = IntentValidator().validate(
        extraction(party_size=None, preferred_time=None),
        REFERENCE_TIME,
    )

    assert result.clarification_question == (
        "Could you provide the party size and time or time range?"
    )


def test_vague_evening_request_asks_for_a_time_rather_than_inventing_one() -> None:
    """The prompt tells the model to leave 'evening' unresolved; that must ask."""

    result = IntentValidator().validate(
        extraction(preferred_time=None, time_window=None),
        REFERENCE_TIME,
    )

    assert result.missing_fields == [MissingField.TIME]
    assert result.clarification_question == "What time or time range works for you?"


def test_malformed_window_is_dropped_when_a_valid_time_exists() -> None:
    result = IntentValidator().validate(
        extraction(preferred_time="19:00", time_window={"start": "21:00", "end": "18:00"}),
        REFERENCE_TIME,
    )

    assert result.is_ready is True
    assert result.time_window is None
    assert result.preferred_time == "19:00"
