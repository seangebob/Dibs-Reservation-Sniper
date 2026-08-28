"""Shared state for the mock booking provider.

The mock adapter generates slots deterministically, but the *state* -- which
slots are published, which are booked, and the idempotency records that make a
booking replayable -- must be one thing shared by every adapter in a process
(the API and each worker child), not a per-adapter dictionary. Otherwise two
processes could book the same slot, or a redelivery could miss a booking made
by the other side.

This module is that shared state. The in-memory implementation here keeps it
under one lock; an equivalent Redis implementation (with the same decisions)
lands alongside it so a multi-process deployment shares one store. Capacity is
bounded so a long-running demo cannot grow without limit: it counts generated
*unbooked* slots and evicts the oldest idle, unpinned ones under pressure.
Booking, tombstone, and idempotency records are protected instead by a much
longer retention window, so replay protection outlives ordinary slot churn.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from backend.db.repositories import mock_booking_scripts
from backend.integrations.base import (
    ReconciliationResult,
    ReconciliationStatus,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.models.reservation import AvailabilitySlot, BookingConfirmation


#: How long a search operation's admitted slots stay pinned against eviction.
#: Finite so a crashed operation's pins expire on their own rather than pinning
#: state forever.
_OPERATION_PIN_SECONDS = 120.0

#: Extra margin on the native key TTL past the logical protection deadline, so a
#: backstop expiry can never remove a record before coordinated cleanup does.
_CLEANUP_GRACE_SECONDS = 3_600.0

#: Every mock-state key lives under this one prefix on a shared Redis primary.
MOCK_KEY_PREFIX = "dibs:mock"

ConfirmationFactory = Callable[[AvailabilitySlot], BookingConfirmation]


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


@dataclass(frozen=True, slots=True)
class CleanupCounts:
    """What one bounded cleanup pass removed."""

    idle_slots: int
    expired_records: int


class MockBookingStateRepository(Protocol):
    """The shared, atomic mock-provider state every adapter in a process uses."""

    async def publish_and_filter(
        self,
        candidates: list[AvailabilitySlot],
        operation_id: str,
        now: datetime,
    ) -> list[AvailabilitySlot]:
        """Admit deterministic candidates under capacity; return the available."""
        ...

    async def get_booking(
        self, idempotency_key: str, now: datetime
    ) -> BookingConfirmation | None:
        """Return a still-protected booking for a key, or None."""
        ...

    async def reconcile_booking(
        self,
        idempotency_key: str,
        booking_permit_id: str | None,
        now: datetime,
    ) -> ReconciliationResult:
        """Authoritatively resolve whether a booking exists for a key."""
        ...

    async def book_slot(
        self,
        slot_id: str,
        idempotency_key: str,
        confirmation_factory: ConfirmationFactory,
        now: datetime,
    ) -> BookingConfirmation:
        """Book one admitted slot idempotently and protect the record."""
        ...

    async def release_operation(self, operation_id: str) -> None:
        """Release a search operation's pins early."""
        ...

    async def cleanup(self, now: datetime, batch_size: int) -> CleanupCounts:
        """Remove idle unbooked slots and expired protected records, bounded."""
        ...


@dataclass(slots=True)
class _SlotEntry:
    slot: AvailabilitySlot
    last_touch: datetime


@dataclass(frozen=True, slots=True)
class _Booking:
    confirmation: BookingConfirmation
    protected_until: datetime


@dataclass(frozen=True, slots=True)
class _Tombstone:
    key: str
    protected_until: datetime


@dataclass(frozen=True, slots=True)
class _Pin:
    #: Each slot is pinned by at most one operation -- the most recent search to
    #: return it -- so pin ownership is a single value per slot, which is what
    #: lets the Redis store reproduce the identical eviction victim.
    operation_id: str
    expires_at: datetime


class InMemoryMockBookingStateRepository:
    """One-process shared mock state, serialized under a single lock.

    Equivalent to the Redis implementation within a process; it makes no claim
    that its state survives process loss, and it exists so every adapter and
    service built in one process observes exactly one booking/idempotency store.
    """

    def __init__(
        self,
        *,
        capacity: int,
        idle_ttl_seconds: float,
        retention_seconds: float,
        pin_seconds: float = _OPERATION_PIN_SECONDS,
    ) -> None:
        self._capacity = capacity
        self._idle_ttl = timedelta(seconds=idle_ttl_seconds)
        self._retention = timedelta(seconds=retention_seconds)
        self._pin = timedelta(seconds=pin_seconds)
        self._slots: dict[str, _SlotEntry] = {}
        self._tombstones: dict[str, _Tombstone] = {}
        self._bookings: dict[str, _Booking] = {}
        #: slot_id -> its single current pin.
        self._pins: dict[str, _Pin] = {}
        self._lock = asyncio.Lock()

    async def publish_and_filter(
        self,
        candidates: list[AvailabilitySlot],
        operation_id: str,
        now: datetime,
    ) -> list[AvailabilitySlot]:
        async with self._lock:
            self._expire_pins(now)
            admitted: set[str] = set()
            result: list[AvailabilitySlot] = []
            for slot in candidates:
                slot_id = slot.slot_id
                tomb = self._tombstones.get(slot_id)
                if tomb is not None and tomb.protected_until > now:
                    # A protected booked slot is never re-published, so a search
                    # can never resurrect a reservation someone else holds.
                    continue
                entry = self._slots.get(slot_id)
                if entry is not None:
                    entry.last_touch = now
                    admitted.add(slot_id)
                    result.append(entry.slot)
                    continue
                if len(self._slots) >= self._capacity and not self._evict_one(
                    now, protected=self._pinned_ids(now) | admitted
                ):
                    # At capacity with nothing evictable: deterministically omit
                    # this candidate rather than exceed capacity or evict a pin.
                    continue
                self._slots[slot_id] = _SlotEntry(slot=slot, last_touch=now)
                admitted.add(slot_id)
                result.append(slot)

            # Pin every admitted slot to this operation, transferring ownership
            # from any earlier operation that had returned the same slot.
            for slot_id in admitted:
                self._pins[slot_id] = _Pin(
                    operation_id=operation_id, expires_at=now + self._pin
                )
            return result

    async def get_booking(
        self, idempotency_key: str, now: datetime
    ) -> BookingConfirmation | None:
        async with self._lock:
            booking = self._bookings.get(idempotency_key)
            if booking is None or booking.protected_until <= now:
                return None
            return booking.confirmation

    async def reconcile_booking(
        self,
        idempotency_key: str,
        booking_permit_id: str | None,
        now: datetime,
    ) -> ReconciliationResult:
        async with self._lock:
            booking = self._bookings.get(idempotency_key)
            if booking is not None and booking.protected_until > now:
                return ReconciliationResult(
                    ReconciliationStatus.CONFIRMED, booking.confirmation
                )
            # The mock is the system of record, so absence is authoritative.
            return ReconciliationResult(ReconciliationStatus.DEFINITIVELY_ABSENT)

    async def book_slot(
        self,
        slot_id: str,
        idempotency_key: str,
        confirmation_factory: ConfirmationFactory,
        now: datetime,
    ) -> BookingConfirmation:
        async with self._lock:
            existing = self._bookings.get(idempotency_key)
            if existing is not None and existing.protected_until > now:
                return existing.confirmation

            tomb = self._tombstones.get(slot_id)
            if (
                tomb is not None
                and tomb.protected_until > now
                and tomb.key != idempotency_key
            ):
                raise SlotUnavailableError(
                    f"Mock slot is already booked: {slot_id}"
                )

            entry = self._slots.get(slot_id)
            if entry is None:
                raise SlotNotFoundError(f"Unknown mock slot: {slot_id}")

            protected_until = now + self._retention
            confirmation = confirmation_factory(entry.slot)
            self._bookings[idempotency_key] = _Booking(
                confirmation=confirmation, protected_until=protected_until
            )
            self._tombstones[slot_id] = _Tombstone(
                key=idempotency_key, protected_until=protected_until
            )
            del self._slots[slot_id]
            self._unpin_slot(slot_id)
            return confirmation

    async def release_operation(self, operation_id: str) -> None:
        async with self._lock:
            self._pins = {
                slot_id: pin
                for slot_id, pin in self._pins.items()
                if pin.operation_id != operation_id
            }

    async def cleanup(self, now: datetime, batch_size: int) -> CleanupCounts:
        async with self._lock:
            self._expire_pins(now)
            pinned = self._pinned_ids(now)
            idle = [
                slot_id
                for slot_id, entry in self._slots.items()
                if slot_id not in pinned
                and entry.last_touch + self._idle_ttl <= now
            ]
            for slot_id in idle[:batch_size]:
                del self._slots[slot_id]

            expired_keys = [
                key
                for key, booking in self._bookings.items()
                if booking.protected_until <= now
            ]
            for key in expired_keys[:batch_size]:
                del self._bookings[key]
            expired_tombs = [
                slot_id
                for slot_id, tomb in self._tombstones.items()
                if tomb.protected_until <= now
            ]
            for slot_id in expired_tombs[:batch_size]:
                del self._tombstones[slot_id]

            return CleanupCounts(
                idle_slots=len(idle[:batch_size]),
                expired_records=len(expired_keys[:batch_size]),
            )

    # -- internals ----------------------------------------------------------

    def _expire_pins(self, now: datetime) -> None:
        self._pins = {
            slot_id: pin
            for slot_id, pin in self._pins.items()
            if pin.expires_at > now
        }

    def _pinned_ids(self, now: datetime) -> set[str]:
        return {
            slot_id
            for slot_id, pin in self._pins.items()
            if pin.expires_at > now
        }

    def _evict_one(self, now: datetime, *, protected: set[str]) -> bool:
        """Remove the oldest evictable unbooked slot; return whether one went."""

        oldest_id: str | None = None
        oldest_touch: datetime | None = None
        for slot_id, entry in self._slots.items():
            if slot_id in protected:
                continue
            if oldest_touch is None or entry.last_touch < oldest_touch:
                oldest_id, oldest_touch = slot_id, entry.last_touch
        if oldest_id is None:
            return False
        del self._slots[oldest_id]
        return True

    def _unpin_slot(self, slot_id: str) -> None:
        self._pins.pop(slot_id, None)


class RedisMockBookingStateRepository:
    """Redis-backed shared mock state, one atomic script per decision.

    Behaviorally equivalent to the in-memory store for every generated trace:
    the same capacity, eviction victim, pin ownership, booking protection, and
    cleanup decisions, verified by a differential oracle. The client is injected
    so it works against a real primary or a Lua-capable test double.
    """

    _SCRIPT_SOURCES = {
        "publish": mock_booking_scripts.PUBLISH_AND_FILTER,
        "book": mock_booking_scripts.BOOK_SLOT,
        "release": mock_booking_scripts.RELEASE_OPERATION,
        "cleanup": mock_booking_scripts.CLEANUP,
    }

    def __init__(
        self,
        client: Any,
        *,
        capacity: int,
        idle_ttl_seconds: float,
        retention_seconds: float,
        pin_seconds: float = _OPERATION_PIN_SECONDS,
        prefix: str = MOCK_KEY_PREFIX,
    ) -> None:
        self._client = client
        self._capacity = capacity
        self._idle_ttl_ms = int(idle_ttl_seconds * 1000)
        self._retention_ms = int(retention_seconds * 1000)
        self._pin_ms = int(pin_seconds * 1000)
        self._grace_ms = int(_CLEANUP_GRACE_SECONDS * 1000)
        self._prefix = prefix
        self._scripts: dict[str, Any] = {}

    def _script(self, name: str) -> Any:
        script = self._scripts.get(name)
        if script is None:
            script = self._client.register_script(self._SCRIPT_SOURCES[name])
            self._scripts[name] = script
        return script

    async def publish_and_filter(
        self,
        candidates: list[AvailabilitySlot],
        operation_id: str,
        now: datetime,
    ) -> list[AvailabilitySlot]:
        args: list[Any] = [
            self._prefix,
            operation_id,
            str(_to_ms(now)),
            str(self._pin_ms),
            str(self._capacity),
        ]
        for slot in candidates:
            args.append(slot.slot_id)
            args.append(slot.model_dump_json())
        out = await self._script("publish")(keys=[], args=args)
        return [AvailabilitySlot.model_validate_json(_text(raw)) for raw in out]

    async def get_booking(
        self, idempotency_key: str, now: datetime
    ) -> BookingConfirmation | None:
        raw = await self._client.get(f"{self._prefix}:booking:{idempotency_key}")
        if raw is None:
            return None
        score = await self._client.zscore(
            f"{self._prefix}:bookexp", idempotency_key
        )
        if score is None or int(score) <= _to_ms(now):
            return None
        return BookingConfirmation.model_validate_json(_text(raw))

    async def reconcile_booking(
        self,
        idempotency_key: str,
        booking_permit_id: str | None,
        now: datetime,
    ) -> ReconciliationResult:
        booking = await self.get_booking(idempotency_key, now)
        if booking is not None:
            return ReconciliationResult(
                ReconciliationStatus.CONFIRMED, booking
            )
        return ReconciliationResult(ReconciliationStatus.DEFINITIVELY_ABSENT)

    async def book_slot(
        self,
        slot_id: str,
        idempotency_key: str,
        confirmation_factory: ConfirmationFactory,
        now: datetime,
    ) -> BookingConfirmation:
        raw = await self._client.get(f"{self._prefix}:slot:{slot_id}")
        confirmation_json = ""
        if raw is not None:
            slot = AvailabilitySlot.model_validate_json(_text(raw))
            confirmation_json = confirmation_factory(slot).model_dump_json()

        out = await self._script("book")(
            keys=[],
            args=[
                self._prefix,
                slot_id,
                idempotency_key,
                confirmation_json,
                str(_to_ms(now) + self._retention_ms),
                str(_to_ms(now)),
                str(self._grace_ms),
            ],
        )
        code = _text(out[0])
        if code in ("BOOKED", "EXISTING"):
            return BookingConfirmation.model_validate_json(_text(out[1]))
        if code == "UNAVAILABLE":
            raise SlotUnavailableError(f"Mock slot is already booked: {slot_id}")
        raise SlotNotFoundError(f"Unknown mock slot: {slot_id}")

    async def release_operation(self, operation_id: str) -> None:
        await self._script("release")(
            keys=[], args=[self._prefix, operation_id]
        )

    async def cleanup(self, now: datetime, batch_size: int) -> CleanupCounts:
        now_ms = _to_ms(now)
        out = await self._script("cleanup")(
            keys=[],
            args=[
                self._prefix,
                str(now_ms),
                str(now_ms - self._idle_ttl_ms),
                str(batch_size),
            ],
        )
        return CleanupCounts(idle_slots=int(out[0]), expired_records=int(out[1]))


def in_memory_mock_state(
    *,
    capacity: int,
    idle_ttl_seconds: float,
    retention_seconds: float,
    clock: Callable[[], datetime] | None = None,
) -> InMemoryMockBookingStateRepository:
    """Build the one shared in-memory mock store for a process.

    `clock` is accepted for symmetry with the other repositories; the caller
    passes `now` into each method, so the store itself never reads the wall
    clock and stays deterministic under tests.
    """

    _ = clock  # reserved; state methods take an explicit `now`
    return InMemoryMockBookingStateRepository(
        capacity=capacity,
        idle_ttl_seconds=idle_ttl_seconds,
        retention_seconds=retention_seconds,
    )
