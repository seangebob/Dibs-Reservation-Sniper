"""Weekday phrases are turned into dates in code, not by the model."""

from datetime import date

import pytest

from backend.orchestrator.weekdays import dates_for_weekday


FRIDAY = date(2026, 11, 20)
SATURDAY = date(2026, 11, 21)


@pytest.mark.parametrize(
    "prompt",
    [
        "Table for 4 at Bauer Saturday at 7",
        "table for 4 this saturday at 7",
        "Watch Bauer for 2 this coming Saturday between 6 and 9",
        "THIS SATURDAY, 4 people, 7pm",
        "Saturday's fine, 2 people at 7",
    ],
)
def test_saturday_and_this_saturday_mean_the_nearest_one(prompt: str) -> None:
    assert dates_for_weekday(prompt, FRIDAY) == (date(2026, 11, 21),)


@pytest.mark.parametrize(
    "prompt",
    [
        "Book Bauer for 4 next Saturday at 7",
        "watch bauer for 2 next sat. between 6 and 9",
        "NEXT SATURDAY for 4",
    ],
)
def test_next_saturday_offers_both_saturdays(prompt: str) -> None:
    assert dates_for_weekday(prompt, FRIDAY) == (
        date(2026, 11, 21),
        date(2026, 11, 28),
    )


def test_next_saturday_said_on_a_saturday_is_a_week_out() -> None:
    assert dates_for_weekday("next Saturday at 7", SATURDAY) == (date(2026, 11, 28),)


def test_the_days_own_name_means_today() -> None:
    assert dates_for_weekday("Bauer for 2 Friday at 9", FRIDAY) == (FRIDAY,)
    assert dates_for_weekday("Bauer for 2 this Friday at 9", FRIDAY) == (FRIDAY,)


def test_every_weekday_is_recognised() -> None:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    found = [dates_for_weekday(f"table {name}", FRIDAY)[0] for name in names]

    assert [day.weekday() for day in found] == list(range(7))
    assert all(FRIDAY <= day < date(2026, 11, 27) for day in found)


def test_bare_abbreviations_that_are_ordinary_words_are_ignored() -> None:
    prompt = "We sat in the sun last time; table on Sunday for 2"

    assert dates_for_weekday(prompt, FRIDAY) == (date(2026, 11, 22),)


@pytest.mark.parametrize(
    "prompt",
    [
        "Table for 4 at 7",
        "Saturday, November 28 at 7",
        "next Saturday Nov 28 at 7",
        "Saturday 28 Nov",
        "Saturday the 28th for 4",
        "next Saturday 11/28",
        "next Saturday (2026-11-28)",
        "Saturday next week at 7",
        "a week from Saturday",
        "Saturday after next",
        "tomorrow (Saturday) at 7",
        "tonight or Saturday",
        "Friday or Saturday for 2",
        "this Saturday or next Saturday",
        "last Saturday",
        "every Saturday at 7",
        "the following Saturday",
    ],
)
def test_anything_more_involved_is_left_to_the_model(prompt: str) -> None:
    assert dates_for_weekday(prompt, FRIDAY) == ()
