from pydantic import ValidationError
import pytest

from backend.models.reservation import AvailabilitySlot
from backend.orchestrator.schemas import (
    IntentAction,
    ReservationExtraction,
    TimeWindow,
    VenueType,
)


def test_extraction_accepts_restaurant_and_recreation_fields() -> None:
    extraction = ReservationExtraction(
        action=IntentAction.CREATE_WATCH,
        venue_name="Grand River Rocks",
        venue_type=VenueType.RECREATION,
        party_size=2,
        date="2026-08-22",
        preferred_time=None,
        time_window=TimeWindow(start="18:00", end="21:00"),
        duration_minutes=120,
        special_requests=["shoe rentals"],
    )

    assert extraction.time_window is not None
    assert extraction.time_window.end == "21:00"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("party_size", 0),
        ("date", "22 August"),
        ("preferred_time", "7 pm"),
        ("duration_minutes", 5),
    ],
)
def test_extraction_rejects_invalid_values(field: str, value: object) -> None:
    data: dict[str, object] = {
        "action": "BOOK_RESERVATION",
        "venue_name": "Cote",
        "venue_type": "RESTAURANT",
        "party_size": 4,
        "date": "2026-08-22",
        "preferred_time": "19:00",
        "time_window": None,
        "duration_minutes": None,
        "special_requests": [],
    }
    data[field] = value

    with pytest.raises(ValidationError):
        ReservationExtraction.model_validate(data)


def test_impossible_calendar_date_survives_extraction_for_the_validator() -> None:
    """A well-formed but unreal date must not fail the provider call.

    It is handled deterministically downstream so the user is asked for a
    real date instead of receiving a gateway error.
    """

    extraction = ReservationExtraction.model_validate(
        {
            "action": "BOOK_RESERVATION",
            "venue_name": "Cote",
            "venue_type": "RESTAURANT",
            "party_size": 4,
            "date": "2026-02-30",
            "preferred_time": "19:00",
            "time_window": None,
            "duration_minutes": None,
            "special_requests": [],
        }
    )

    assert extraction.date == "2026-02-30"
    assert extraction.has_valid_date is False


def test_extraction_forbids_model_invented_fields() -> None:
    with pytest.raises(ValidationError):
        ReservationExtraction.model_validate(
            {
                "action": "BOOK_RESERVATION",
                "venue_name": "Cote",
                "venue_type": "RESTAURANT",
                "party_size": 4,
                "date": "2026-08-22",
                "preferred_time": "19:00",
                "time_window": None,
                "duration_minutes": None,
                "special_requests": [],
                "confirmed_available": True,
            }
        )


def test_absent_date_is_not_treated_as_valid() -> None:
    extraction = ReservationExtraction(
        action=IntentAction.BOOK_RESERVATION,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        party_size=4,
        date=None,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )

    assert extraction.has_valid_date is False


# --- a slot's link to the provider's booking page ---------------------------


_SLOT = dict(
    slot_id="slot_1",
    provider="kw-tables",
    venue_name="The Bauer Kitchen",
    venue_type="RESTAURANT",
    date="2026-12-01",
    start_time="19:00",
    end_time=None,
    party_size=2,
    max_party_size=4,
)


def test_a_slot_needs_no_booking_url() -> None:
    assert AvailabilitySlot(**_SLOT).booking_url is None


def test_a_slot_keeps_an_https_booking_url() -> None:
    url = "https://book.example.com/r/42?party=2"

    assert AvailabilitySlot(**_SLOT, booking_url=url).booking_url == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,hi",
        "http://book.example.com/r/42",
        "https://user:secret@book.example.com/r/42",
        "https:///no-host",
        "https://book.example.com/a b",
    ],
)
def test_a_slot_rejects_anything_but_a_plain_https_booking_url(url: str) -> None:
    with pytest.raises(ValueError):
        AvailabilitySlot(**_SLOT, booking_url=url)
