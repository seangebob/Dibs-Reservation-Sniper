from datetime import date

import pytest

from backend.data.venues import (
    DEFAULT_VENUE_PROFILE,
    STATUTORY_CLOSURES,
    VENUE_CATALOG,
    VenueResolutionStatus,
    profile_for,
    resolve_venue,
)
from backend.orchestrator.schemas import VenueType


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("The Bauer Kitchen", "The Bauer Kitchen"),
        ("bauer kitchen", "The Bauer Kitchen"),
        ("BAUER", "The Bauer Kitchen"),
        ("Bauer  Kitchen ", "The Bauer Kitchen"),
        ("grr", "Grand River Rocks"),
        ("Grand River Rocks", "Grand River Rocks"),
        ("chicopee", "Chicopee Tube Park"),
        ("Ethel's Lounge", "Ethel's Lounge"),
        ("ethels lounge", "Ethel's Lounge"),
    ],
)
def test_unique_names_and_aliases_resolve_to_one_venue(
    written: str,
    expected: str,
) -> None:
    resolution = resolve_venue(written)

    assert resolution.status is VenueResolutionStatus.RESOLVED
    assert resolution.venue is not None
    assert resolution.venue.name == expected


@pytest.mark.parametrize(
    ("written", "expected_names"),
    [
        ("kitchen", {"The Bauer Kitchen", "Proof Kitchen + Lounge"}),
        ("steak house", {"The Charcoal Steak House", "Golf's Steak House"}),
        ("grand", {"Grand Trunk Saloon", "Grand River Rocks"}),
    ],
)
def test_partial_names_matching_several_venues_are_ambiguous(
    written: str,
    expected_names: set[str],
) -> None:
    resolution = resolve_venue(written)

    assert resolution.status is VenueResolutionStatus.AMBIGUOUS
    assert resolution.venue is None
    assert {venue.name for venue in resolution.candidates} == expected_names


@pytest.mark.parametrize("written", ["Cote", "Côte", "  ", "!!!", "Nonexistent Bistro"])
def test_venues_outside_the_catalog_are_unknown_not_guessed(written: str) -> None:
    resolution = resolve_venue(written)

    assert resolution.status is VenueResolutionStatus.UNKNOWN
    assert resolution.venue is None
    assert resolution.candidates == ()


def test_unknown_venues_fall_back_to_the_generic_profile() -> None:
    assert profile_for("Cote") is DEFAULT_VENUE_PROFILE
    assert profile_for("Grand River Rocks") is not DEFAULT_VENUE_PROFILE


def test_catalog_venue_names_are_individually_resolvable() -> None:
    for venue in VENUE_CATALOG:
        resolution = resolve_venue(venue.name)

        assert resolution.status is VenueResolutionStatus.RESOLVED, venue.name
        assert resolution.venue is venue


def test_hours_differ_between_weekdays_and_weekends() -> None:
    charcoal = resolve_venue("charcoal").venue
    assert charcoal is not None

    assert charcoal.profile.hours_for(date(2026, 8, 24)) is None  # Monday
    friday = charcoal.profile.hours_for(date(2026, 8, 21))
    sunday = charcoal.profile.hours_for(date(2026, 8, 23))

    assert friday is not None and friday.close_time == "23:00"
    assert sunday is not None and sunday.close_time == "21:00"
    assert sunday.open_time != friday.open_time


def test_statutory_closures_apply_to_catalog_and_default_profiles() -> None:
    bauer = resolve_venue("bauer").venue
    assert bauer is not None

    for closed in ("2026-12-25", "2026-12-26", "2026-01-01"):
        assert closed in STATUTORY_CLOSURES
        assert bauer.profile.hours_for(date.fromisoformat(closed)) is None
        assert DEFAULT_VENUE_PROFILE.hours_for(date.fromisoformat(closed)) is None


def test_sold_out_dates_are_open_but_unbookable() -> None:
    valentines = date(2026, 2, 14)

    assert DEFAULT_VENUE_PROFILE.hours_for(valentines) is not None
    assert DEFAULT_VENUE_PROFILE.is_sold_out(valentines) is True
    assert DEFAULT_VENUE_PROFILE.is_sold_out(date(2026, 8, 22)) is False


def test_recreation_and_restaurant_types_come_from_the_catalog() -> None:
    assert resolve_venue("grr").venue.venue_type is VenueType.RECREATION
    assert resolve_venue("proof").venue.venue_type is VenueType.RESTAURANT
