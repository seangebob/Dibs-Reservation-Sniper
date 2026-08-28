import asyncio
from datetime import UTC, datetime

import pytest

from backend.integrations.base import SlotNotFoundError, SlotUnavailableError
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery, BookingStatus
from backend.orchestrator.schemas import TimeWindow, VenueType


def query(**updates: object) -> AvailabilityQuery:
    data: dict[str, object] = {
        "venue_name": "Grand River Rocks",
        "venue_type": VenueType.RECREATION,
        "market": "Kitchener-Waterloo-Cambridge, ON",
        "party_size": 2,
        "date": "2026-08-22",
        "preferred_time": None,
        "time_window": TimeWindow(start="18:00", end="21:00"),
        "duration_minutes": 120,
        "special_requests": [],
    }
    data.update(updates)
    return AvailabilityQuery.model_validate(data)


def test_mock_search_returns_deterministic_quarter_hour_slots() -> None:
    adapter = MockBookingAdapter()

    first = asyncio.run(adapter.search_availability(query()))
    second = asyncio.run(adapter.search_availability(query()))

    # Grand River Rocks closes at 22:00 on Saturdays, so a 120-minute booking
    # cannot start after 20:00 even though the request runs to 21:00.
    assert [slot.start_time for slot in first] == [
        "18:00",
        "18:15",
        "18:30",
        "18:45",
        "19:00",
        "19:15",
        "19:30",
        "19:45",
        "20:00",
    ]
    assert [slot.slot_id for slot in first] == [slot.slot_id for slot in second]
    assert first[0].end_time == "20:00"


def test_mock_booking_is_idempotent_and_removes_booked_availability() -> None:
    fixed_time = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    adapter = MockBookingAdapter(clock=lambda: fixed_time)
    slots = asyncio.run(adapter.search_availability(query()))

    first = asyncio.run(
        adapter.book_slot(slots[0].slot_id, idempotency_key="request-1")
    )
    repeated = asyncio.run(
        adapter.book_slot(slots[0].slot_id, idempotency_key="request-1")
    )

    assert first == repeated
    assert first.status is BookingStatus.MOCK_CONFIRMED
    assert first.created_at == fixed_time
    remaining = asyncio.run(adapter.search_availability(query()))
    assert slots[0].slot_id not in {slot.slot_id for slot in remaining}

    with pytest.raises(SlotUnavailableError):
        asyncio.run(
            adapter.book_slot(slots[0].slot_id, idempotency_key="request-2")
        )


def test_mock_adapter_can_simulate_no_availability() -> None:
    adapter = MockBookingAdapter(unavailable_venues={"Grand River Rocks"})

    assert asyncio.run(adapter.search_availability(query())) == []


def test_preferred_time_inside_window_is_prioritized() -> None:
    adapter = MockBookingAdapter()
    slots = asyncio.run(
        adapter.search_availability(
            query(preferred_time="19:00")
        )
    )

    assert slots[0].start_time == "19:00"


def test_query_rejects_preferred_time_outside_window() -> None:
    with pytest.raises(ValueError, match="inside the time window"):
        query(preferred_time="23:00")


def restaurant_query(**updates: object) -> AvailabilityQuery:
    data: dict[str, object] = {
        "venue_name": "The Bauer Kitchen",
        "venue_type": VenueType.RESTAURANT,
        "market": "Kitchener-Waterloo-Cambridge, ON",
        "party_size": 2,
        "date": "2026-08-22",
        "preferred_time": None,
        "time_window": TimeWindow(start="17:00", end="21:00"),
        "duration_minutes": None,
        "special_requests": [],
    }
    data.update(updates)
    return AvailabilityQuery.model_validate(data)


def test_slots_sit_on_a_fifteen_minute_grid() -> None:
    slots = asyncio.run(MockBookingAdapter().search_availability(restaurant_query()))

    assert slots
    for slot in slots:
        hours, minutes = (int(part) for part in slot.start_time.split(":"))
        assert minutes % 15 == 0
        assert 0 <= hours < 24


def test_slots_stay_inside_venue_opening_hours() -> None:
    # The Bauer Kitchen opens at 11:30 and closes at 22:00 on a Saturday, and
    # holds a table for 90 minutes, so nothing may start before 11:30 or
    # after 20:30 however wide the request is.
    slots = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(time_window=TimeWindow(start="00:00", end="23:45"))
        )
    )

    assert slots
    assert all("11:30" <= slot.start_time <= "20:30" for slot in slots)


def test_time_outside_opening_hours_returns_nothing() -> None:
    adapter = MockBookingAdapter()

    assert (
        asyncio.run(
            adapter.search_availability(
                restaurant_query(preferred_time="03:00", time_window=None)
            )
        )
        == []
    )


def test_closed_weekday_returns_no_slots() -> None:
    # The Charcoal Steak House is closed on Mondays.
    monday = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(
                venue_name="The Charcoal Steak House",
                date="2026-08-24",
                time_window=TimeWindow(start="17:00", end="21:00"),
            )
        )
    )
    tuesday = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(
                venue_name="The Charcoal Steak House",
                date="2026-08-25",
                time_window=TimeWindow(start="17:00", end="21:00"),
            )
        )
    )

    assert monday == []
    assert tuesday


def test_weekend_and_weekday_availability_differ() -> None:
    adapter = MockBookingAdapter()
    wide = TimeWindow(start="06:00", end="23:45")

    friday = asyncio.run(
        adapter.search_availability(
            restaurant_query(date="2026-08-21", time_window=wide)
        )
    )
    sunday = asyncio.run(
        adapter.search_availability(
            restaurant_query(date="2026-08-23", time_window=wide)
        )
    )

    # Sunday brunch opens earlier and the kitchen closes earlier.
    assert sunday[0].start_time < friday[0].start_time
    assert sunday[-1].start_time < friday[-1].start_time


@pytest.mark.parametrize("holiday", ["2026-12-25", "2026-12-26", "2026-01-01"])
def test_statutory_holidays_return_no_availability(holiday: str) -> None:
    slots = asyncio.run(
        MockBookingAdapter().search_availability(restaurant_query(date=holiday))
    )

    assert slots == []


def test_fully_booked_date_returns_no_slots_rather_than_open_ones() -> None:
    slots = asyncio.run(
        MockBookingAdapter().search_availability(restaurant_query(date="2026-02-14"))
    )

    assert slots == []


def test_party_larger_than_the_venue_gets_nothing() -> None:
    adapter = MockBookingAdapter()

    # Golf's Steak House seats at most 8.
    assert (
        asyncio.run(
            adapter.search_availability(
                restaurant_query(
                    venue_name="Golf's Steak House",
                    date="2026-08-22",
                    party_size=20,
                    time_window=TimeWindow(start="17:00", end="21:00"),
                )
            )
        )
        == []
    )


def test_individual_slots_carry_their_own_table_size() -> None:
    adapter = MockBookingAdapter()
    wide = TimeWindow(start="11:00", end="21:00")

    small = asyncio.run(adapter.search_availability(restaurant_query(party_size=2, time_window=wide)))
    large = asyncio.run(adapter.search_availability(restaurant_query(party_size=8, time_window=wide)))

    assert {slot.max_party_size for slot in small} != {8}
    assert all(slot.max_party_size >= 8 for slot in large)
    # A larger party can only fit a subset of the times a couple can take.
    assert len(large) < len(small)
    assert {slot.start_time for slot in large} <= {slot.start_time for slot in small}


def test_capacity_is_stable_across_adapter_instances() -> None:
    first = asyncio.run(MockBookingAdapter().search_availability(restaurant_query()))
    second = asyncio.run(MockBookingAdapter().search_availability(restaurant_query()))

    assert [(slot.start_time, slot.max_party_size) for slot in first] == [
        (slot.start_time, slot.max_party_size) for slot in second
    ]


def test_unaligned_preferred_time_snaps_to_the_grid() -> None:
    adapter = MockBookingAdapter()

    early = asyncio.run(
        adapter.search_availability(
            restaurant_query(preferred_time="19:07", time_window=None)
        )
    )
    late = asyncio.run(
        adapter.search_availability(
            restaurant_query(preferred_time="19:08", time_window=None)
        )
    )

    assert [slot.start_time for slot in early] == ["19:00"]
    assert [slot.start_time for slot in late] == ["19:15"]


def test_duration_that_would_run_past_closing_is_not_offered() -> None:
    # Waterloo Bowling Lanes closes at 21:00 on Sundays.
    slots = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(
                venue_name="Waterloo Bowling Lanes",
                venue_type=VenueType.RECREATION,
                date="2026-08-23",
                time_window=TimeWindow(start="19:00", end="21:00"),
                duration_minutes=120,
            )
        )
    )

    assert [slot.start_time for slot in slots] == ["19:00"]
    assert slots[0].end_time == "21:00"


def test_booking_an_unknown_slot_id_is_reported_distinctly() -> None:
    with pytest.raises(SlotNotFoundError):
        asyncio.run(
            MockBookingAdapter().book_slot("mock_missing", idempotency_key="k")
        )


def test_venue_closing_after_midnight_offers_late_slots() -> None:
    # Waterloo Bowling Lanes closes at 00:00 on Fridays, so a 22:45 start is
    # legitimate rather than a wrap-around to the previous morning.
    slots = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(
                venue_name="Waterloo Bowling Lanes",
                venue_type=VenueType.RECREATION,
                date="2026-08-21",
                party_size=2,
                time_window=TimeWindow(start="22:00", end="23:45"),
                duration_minutes=60,
            )
        )
    )

    assert [slot.start_time for slot in slots] == [
        "22:00",
        "22:15",
        "22:30",
        "22:45",
        "23:00",
    ]
    assert slots[-1].end_time == "00:00"


def test_duration_longer_than_the_venue_is_open_yields_nothing() -> None:
    slots = asyncio.run(
        MockBookingAdapter().search_availability(
            restaurant_query(
                venue_name="Golf's Steak House",
                date="2026-08-22",
                party_size=2,
                time_window=TimeWindow(start="17:00", end="22:00"),
                duration_minutes=720,
            )
        )
    )

    assert slots == []


# --- shared state across adapters (milestone 3) -----------------------------


def test_two_adapters_over_one_state_share_bookings() -> None:
    """The whole point of task 8: an API adapter and a worker adapter agree."""

    from backend.db.repositories.mock_booking import in_memory_mock_state
    from backend.integrations.base import ReconciliationStatus

    fixed = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    shared = in_memory_mock_state(
        capacity=1000, idle_ttl_seconds=3600.0, retention_seconds=7 * 24 * 3600.0
    )
    api = MockBookingAdapter(state=shared, clock=lambda: fixed)
    worker = MockBookingAdapter(state=shared, clock=lambda: fixed)

    slots = asyncio.run(api.search_availability(restaurant_query()))
    booked = asyncio.run(
        api.book_slot(slots[0].slot_id, idempotency_key="watch:x")
    )

    # The worker, a separate adapter, observes the booking the API made.
    assert asyncio.run(worker.get_booking("watch:x")) == booked
    reconciled = asyncio.run(worker.reconcile_booking("watch:x"))
    assert reconciled.status is ReconciliationStatus.CONFIRMED

    # It is not re-offered, and a different key cannot re-book it.
    remaining = asyncio.run(worker.search_availability(restaurant_query()))
    assert slots[0].slot_id not in {slot.slot_id for slot in remaining}
    with pytest.raises(SlotUnavailableError):
        asyncio.run(worker.book_slot(slots[0].slot_id, idempotency_key="watch:y"))


def test_adapters_without_shared_state_stay_isolated() -> None:
    """Two independent adapters own separate state -- the pre-task-8 behavior."""

    fixed = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    api = MockBookingAdapter(clock=lambda: fixed)
    worker = MockBookingAdapter(clock=lambda: fixed)

    slots = asyncio.run(api.search_availability(restaurant_query()))
    asyncio.run(api.book_slot(slots[0].slot_id, idempotency_key="watch:x"))

    # The worker's own store knows nothing about the API's booking.
    assert asyncio.run(worker.get_booking("watch:x")) is None
