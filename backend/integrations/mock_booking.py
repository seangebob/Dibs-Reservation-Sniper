"""Deterministic in-memory adapter for Milestone 2 development."""

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from backend.integrations.base import (
    ReservationAdapter,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
)


Clock = Callable[[], datetime]


class MockBookingAdapter(ReservationAdapter):
    """Generates reproducible slots and stores mock bookings in memory."""

    def __init__(
        self,
        *,
        unavailable_venues: Iterable[str] = (),
        clock: Clock | None = None,
    ) -> None:
        self._unavailable_venues = {
            venue.strip().casefold() for venue in unavailable_venues
        }
        self._clock = clock or (lambda: datetime.now(UTC))
        self._slots: dict[str, AvailabilitySlot] = {}
        self._booked_slot_ids: set[str] = set()
        self._bookings_by_key: dict[str, BookingConfirmation] = {}
        self._lock = asyncio.Lock()

    async def search_availability(
        self,
        query: AvailabilityQuery,
    ) -> list[AvailabilitySlot]:
        if query.venue_name.casefold() in self._unavailable_venues:
            return []

        slots: list[AvailabilitySlot] = []
        for start_time in self._candidate_times(query):
            end_time = self._end_time(start_time, query.duration_minutes)
            if query.duration_minutes is not None and end_time is None:
                continue

            identity = "|".join(
                (
                    query.market,
                    query.venue_name.casefold(),
                    query.date,
                    start_time,
                    str(query.party_size),
                    str(query.duration_minutes or "none"),
                )
            )
            slot_id = f"mock_{uuid5(NAMESPACE_URL, identity).hex}"
            slot = AvailabilitySlot(
                slot_id=slot_id,
                provider="mock",
                venue_name=query.venue_name,
                venue_type=query.venue_type,
                date=query.date,
                start_time=start_time,
                end_time=end_time,
                party_size=query.party_size,
                available=True,
            )
            self._slots[slot_id] = slot
            if slot_id not in self._booked_slot_ids:
                slots.append(slot)

        return slots

    async def get_booking(
        self,
        idempotency_key: str,
    ) -> BookingConfirmation | None:
        async with self._lock:
            return self._bookings_by_key.get(idempotency_key)

    async def book_slot(
        self,
        slot_id: str,
        *,
        idempotency_key: str,
    ) -> BookingConfirmation:
        async with self._lock:
            existing = self._bookings_by_key.get(idempotency_key)
            if existing is not None:
                return existing

            slot = self._slots.get(slot_id)
            if slot is None:
                raise SlotNotFoundError(f"Unknown mock slot: {slot_id}")
            if slot_id in self._booked_slot_ids:
                raise SlotUnavailableError(f"Mock slot is already booked: {slot_id}")

            booking_id = f"mock_booking_{uuid5(NAMESPACE_URL, idempotency_key).hex}"
            confirmation = BookingConfirmation(
                booking_id=booking_id,
                provider="mock",
                status=BookingStatus.MOCK_CONFIRMED,
                slot=slot,
                created_at=self._clock(),
            )
            self._booked_slot_ids.add(slot_id)
            self._bookings_by_key[idempotency_key] = confirmation
            return confirmation

    @staticmethod
    def _candidate_times(query: AvailabilityQuery) -> list[str]:
        if query.time_window is None:
            return [query.preferred_time] if query.preferred_time is not None else []

        start = MockBookingAdapter._to_minutes(query.time_window.start)
        end = MockBookingAdapter._to_minutes(query.time_window.end)
        candidates = [
            MockBookingAdapter._from_minutes(minutes)
            for minutes in range(start, end + 1, 30)
        ]

        if query.preferred_time is not None:
            if query.preferred_time in candidates:
                candidates.remove(query.preferred_time)
                candidates.insert(0, query.preferred_time)
        return candidates[:12]

    @staticmethod
    def _end_time(start_time: str, duration_minutes: int | None) -> str | None:
        if duration_minutes is None:
            return None
        end_minutes = MockBookingAdapter._to_minutes(start_time) + duration_minutes
        if end_minutes >= 24 * 60:
            return None
        return MockBookingAdapter._from_minutes(end_minutes)

    @staticmethod
    def _to_minutes(value: str) -> int:
        hours, minutes = (int(part) for part in value.split(":"))
        return hours * 60 + minutes

    @staticmethod
    def _from_minutes(value: int) -> str:
        hours, minutes = divmod(value, 60)
        return f"{hours:02d}:{minutes:02d}"
