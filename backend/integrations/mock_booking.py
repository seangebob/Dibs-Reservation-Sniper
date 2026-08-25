"""Deterministic in-memory adapter for Milestone 2 development."""

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from backend.data.venues import (
    MAX_GENERATED_SLOTS,
    SLOT_INTERVAL_MINUTES,
    OpeningHours,
    VenueProfile,
    profile_for,
)
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

#: Per-slot table sizes the mock cycles through, so some slots genuinely
#: cannot seat a large party even when the venue itself could.
SLOT_CAPACITIES: tuple[int, ...] = (2, 4, 6, 8, 10)

_MINUTES_PER_DAY = 24 * 60


class MockBookingAdapter(ReservationAdapter):
    """Generates reproducible slots and stores mock bookings in memory.

    Slots follow the mock venue catalog: they sit on a fifteen-minute grid
    inside that venue's hours for that weekday, skip closed and sold-out
    dates, and carry a per-slot table size. The same query always produces
    the same slot identifiers.
    """

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

        profile = profile_for(query.venue_name)
        day = date.fromisoformat(query.date)
        if profile.is_sold_out(day) or query.party_size > profile.max_party_size:
            return []

        hours = profile.hours_for(day)
        if hours is None:
            return []

        slots: list[AvailabilitySlot] = []
        for start_time in self._candidate_times(query, profile, hours):
            capacity = self._slot_capacity(query, start_time, profile)
            if query.party_size > capacity:
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
                end_time=self._end_time(start_time, query.duration_minutes),
                party_size=query.party_size,
                max_party_size=capacity,
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

    @classmethod
    def _candidate_times(
        cls,
        query: AvailabilityQuery,
        profile: VenueProfile,
        hours: OpeningHours,
    ) -> list[str]:
        """Return bookable start times inside the venue's hours."""

        opens = cls._to_minutes(hours.open_time)
        closes = cls._to_minutes(hours.close_time)
        if closes <= opens:
            closes += _MINUTES_PER_DAY

        stay = query.duration_minutes or profile.minimum_stay_minutes
        latest_start = closes - stay
        if latest_start < opens:
            return []

        if query.time_window is None:
            if query.preferred_time is None:
                return []
            exact = cls._snap_to_grid(cls._to_minutes(query.preferred_time))
            if not opens <= exact <= latest_start:
                return []
            return [cls._from_minutes(exact)]

        first = max(opens, cls._snap_to_grid(cls._to_minutes(query.time_window.start)))
        last = min(latest_start, cls._to_minutes(query.time_window.end))
        candidates = [
            cls._from_minutes(minutes)
            for minutes in range(first, last + 1, SLOT_INTERVAL_MINUTES)
        ]

        if query.preferred_time is not None and query.preferred_time in candidates:
            candidates.remove(query.preferred_time)
            candidates.insert(0, query.preferred_time)
        return candidates[:MAX_GENERATED_SLOTS]

    @staticmethod
    def _slot_capacity(
        query: AvailabilityQuery,
        start_time: str,
        profile: VenueProfile,
    ) -> int:
        """Derive a stable table size for one venue, date, and start time."""

        identity = f"{query.venue_name.casefold()}|{query.date}|{start_time}"
        digest = sha256(identity.encode("utf-8")).digest()
        capacity = SLOT_CAPACITIES[digest[0] % len(SLOT_CAPACITIES)]
        return min(capacity, profile.max_party_size)

    @classmethod
    def _end_time(cls, start_time: str, duration_minutes: int | None) -> str | None:
        if duration_minutes is None:
            return None
        end_minutes = cls._to_minutes(start_time) + duration_minutes
        return cls._from_minutes(end_minutes % _MINUTES_PER_DAY)

    @staticmethod
    def _snap_to_grid(minutes: int) -> int:
        """Round a time to the nearest slot boundary, preferring earlier."""

        offset = minutes % SLOT_INTERVAL_MINUTES
        if offset == 0:
            return minutes
        if offset * 2 <= SLOT_INTERVAL_MINUTES:
            return minutes - offset
        return minutes - offset + SLOT_INTERVAL_MINUTES

    @staticmethod
    def _to_minutes(value: str) -> int:
        hours, minutes = (int(part) for part in value.split(":"))
        return hours * 60 + minutes

    @staticmethod
    def _from_minutes(value: int) -> str:
        hours, minutes = divmod(value, 60)
        return f"{hours:02d}:{minutes:02d}"
