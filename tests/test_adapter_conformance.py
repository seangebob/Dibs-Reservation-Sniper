"""The contract every `ReservationAdapter` must satisfy.

Until now the seam had exactly one implementation, so "provider-neutral" was an
aspiration rather than a tested property: the public models pinned
`provider: Literal["mock"]`, which meant no real adapter could construct a valid
slot at all. These tests run the same suite against the built-in mock AND a
second, deliberately-different in-memory adapter, so the contract is proven by
two implementations rather than asserted by one.

The second adapter is intentionally NOT a copy of the mock. It reports a
different provider name, returns `CONFIRMED` rather than `MOCK_CONFIRMED`, and
takes the conservative `UNKNOWN` reconciliation default -- the shape a real
provider has, and the shape the mock does not.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from backend.integrations.base import (
    ReconciliationStatus,
    ReservationAdapter,
    SlotNotFoundError,
)
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
)
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=2,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


class _SecondAdapter(ReservationAdapter):
    """A minimal second implementation, to prove the seam takes one.

    Deliberately unlike the mock: its own provider name, real `CONFIRMED`
    bookings, and the base class's conservative `UNKNOWN` reconciliation.
    """

    provider_name = "second-provider"

    def __init__(self) -> None:
        self._bookings: dict[str, BookingConfirmation] = {}

    def _slot(self, query: AvailabilityQuery) -> AvailabilitySlot:
        return AvailabilitySlot(
            slot_id="second-slot-1",
            provider=self.provider_name,
            venue_name=query.venue_name,
            venue_type=query.venue_type,
            date=query.date,
            start_time=query.preferred_time or "19:00",
            end_time=None,
            party_size=query.party_size,
            max_party_size=max(query.party_size, 4),
        )

    async def search_availability(
        self, query: AvailabilityQuery
    ) -> list[AvailabilitySlot]:
        return [self._slot(query)]

    async def get_booking(self, idempotency_key: str) -> BookingConfirmation | None:
        return self._bookings.get(idempotency_key)

    async def book_slot(
        self, slot_id: str, *, idempotency_key: str
    ) -> BookingConfirmation:
        existing = self._bookings.get(idempotency_key)
        if existing is not None:
            return existing
        if slot_id != "second-slot-1":
            raise SlotNotFoundError(f"unknown slot: {slot_id}")
        confirmation = BookingConfirmation(
            booking_id=f"second-{idempotency_key}",
            provider=self.provider_name,
            status=BookingStatus.CONFIRMED,
            slot=self._slot(_query()),
            created_at=NOW,
        )
        self._bookings[idempotency_key] = confirmation
        return confirmation


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(params=["mock", "second"])
def adapter(request) -> ReservationAdapter:
    return MockBookingAdapter() if request.param == "mock" else _SecondAdapter()


# --- the contract ----------------------------------------------------------


def test_search_returns_slots_that_match_the_query(adapter) -> None:
    slots = _run(adapter.search_availability(_query()))

    assert slots, "an adapter with availability must return at least one slot"
    for slot in slots:
        assert slot.venue_type is VenueType.RESTAURANT
        assert slot.date == "2026-09-05"
        assert slot.party_size <= slot.max_party_size


def test_every_slot_names_its_own_provider(adapter) -> None:
    """The field that used to be `Literal["mock"]`: each adapter must be able to
    stamp its own identity, and it must be a slug rather than free text."""

    slots = _run(adapter.search_availability(_query()))

    for slot in slots:
        assert slot.provider
        assert slot.provider == slot.provider.lower()
        assert " " not in slot.provider


def test_booking_is_idempotent_under_one_key(adapter) -> None:
    slots = _run(adapter.search_availability(_query()))
    slot_id = slots[0].slot_id

    first = _run(adapter.book_slot(slot_id, idempotency_key="key-1"))
    second = _run(adapter.book_slot(slot_id, idempotency_key="key-1"))

    assert first.booking_id == second.booking_id


def test_a_booking_is_readable_back_by_its_key(adapter) -> None:
    slots = _run(adapter.search_availability(_query()))
    booked = _run(adapter.book_slot(slots[0].slot_id, idempotency_key="key-2"))

    assert _run(adapter.get_booking("key-2")) == booked


def test_an_unknown_key_has_no_booking(adapter) -> None:
    assert _run(adapter.get_booking("never-used")) is None


def test_an_unknown_slot_is_rejected(adapter) -> None:
    with pytest.raises(SlotNotFoundError):
        _run(adapter.book_slot("no-such-slot", idempotency_key="key-3"))


def test_a_confirmation_carries_the_adapters_provider(adapter) -> None:
    slots = _run(adapter.search_availability(_query()))
    booked = _run(adapter.book_slot(slots[0].slot_id, idempotency_key="key-4"))

    assert booked.provider == slots[0].provider


def test_reconciliation_confirms_a_known_booking(adapter) -> None:
    slots = _run(adapter.search_availability(_query()))
    _run(adapter.book_slot(slots[0].slot_id, idempotency_key="key-5"))

    result = _run(adapter.reconcile_booking("key-5"))

    assert result.status is ReconciliationStatus.CONFIRMED
    assert result.confirmation is not None


def test_reconciliation_never_invents_certainty_about_an_absent_booking(
    adapter,
) -> None:
    """The distinction the base class exists to protect: only an adapter that
    can genuinely answer authoritatively may say DEFINITIVELY_ABSENT. Saying it
    wrongly is what would let auto-book double-book someone."""

    result = _run(adapter.reconcile_booking("never-booked"))

    assert result.status in {
        ReconciliationStatus.DEFINITIVELY_ABSENT,
        ReconciliationStatus.UNKNOWN,
    }
    assert result.confirmation is None


def test_the_conservative_default_is_unknown_not_absent() -> None:
    """An adapter that does not override `reconcile_booking` must inherit the
    safe answer, not the convenient one."""

    result = _run(_SecondAdapter().reconcile_booking("never-booked"))

    assert result.status is ReconciliationStatus.UNKNOWN


def test_the_mock_remains_authoritative() -> None:
    """The mock overrides the default precisely because it *can* be certain;
    that is what makes cancellation-safe auto-book testable."""

    result = _run(MockBookingAdapter().reconcile_booking("never-booked"))

    assert result.status is ReconciliationStatus.DEFINITIVELY_ABSENT


def test_a_mock_booking_is_never_mistaken_for_a_real_one() -> None:
    """The two providers must stay distinguishable in the public contract: a
    demo booking must not look like a table a venue is actually holding."""

    mock_slots = _run(MockBookingAdapter().search_availability(_query()))
    mock_adapter = MockBookingAdapter()
    mock_booking = _run(
        mock_adapter.book_slot(
            _run(mock_adapter.search_availability(_query()))[0].slot_id,
            idempotency_key="key-6",
        )
    )
    second = _SecondAdapter()
    real_booking = _run(second.book_slot("second-slot-1", idempotency_key="key-6"))

    assert mock_booking.status is BookingStatus.MOCK_CONFIRMED
    assert real_booking.status is BookingStatus.CONFIRMED
    assert mock_slots[0].provider != real_booking.provider
