from pydantic import ValidationError
import pytest

from reservation_nlp.models import ReservationIntent


def test_complete_intent_is_executable() -> None:
    intent = ReservationIntent(
        restaurant="Cote",
        party_size=4,
        date="2026-08-22",
        preferred_time="19:00",
        missing_info=None,
    )

    assert intent.is_complete is True


def test_missing_data_requires_targeted_question() -> None:
    intent = ReservationIntent(
        restaurant="Cote",
        party_size=None,
        date="2026-08-22",
        preferred_time="19:00",
        missing_info="How many people are in your party?",
    )

    assert intent.is_complete is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("party_size", 0),
        ("date", "2026-02-30"),
        ("preferred_time", "7 pm"),
        ("missing_info", "Please provide the party size"),
    ],
)
def test_invalid_structured_values_are_rejected(field: str, value: object) -> None:
    data: dict[str, object] = {
        "restaurant": "Cote",
        "party_size": 4,
        "date": "2026-08-22",
        "preferred_time": "19:00",
        "missing_info": None,
    }
    data[field] = value
    if field == "missing_info":
        data["party_size"] = None

    with pytest.raises(ValidationError):
        ReservationIntent.model_validate(data)


def test_model_does_not_allow_invented_fields() -> None:
    with pytest.raises(ValidationError):
        ReservationIntent.model_validate(
            {
                "restaurant": "Cote",
                "party_size": 4,
                "date": "2026-08-22",
                "preferred_time": "19:00",
                "missing_info": None,
                "book_immediately": True,
            }
        )
