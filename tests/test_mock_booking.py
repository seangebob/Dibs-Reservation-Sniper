import asyncio
from datetime import UTC, datetime

import pytest

from backend.integrations.base import SlotUnavailableError
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery, BookingStatus
from backend.orchestrator.schemas import TimeWindow, VenueType


def query(**updates: object) -> AvailabilityQuery:
    data: dict[str, object] = {
        "venue_name": "Grand River Rocks",
        "venue_type": VenueType.RECREATION,
        "market": "Kitchener-Waterloo, ON",
        "party_size": 2,
        "date": "2026-08-22",
        "preferred_time": None,
        "time_window": TimeWindow(start="18:00", end="21:00"),
        "duration_minutes": 120,
        "special_requests": [],
    }
    data.update(updates)
    return AvailabilityQuery.model_validate(data)


def test_mock_search_returns_deterministic_half_hour_slots() -> None:
    adapter = MockBookingAdapter()

    first = asyncio.run(adapter.search_availability(query()))
    second = asyncio.run(adapter.search_availability(query()))

    assert [slot.start_time for slot in first] == [
        "18:00",
        "18:30",
        "19:00",
        "19:30",
        "20:00",
        "20:30",
        "21:00",
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
