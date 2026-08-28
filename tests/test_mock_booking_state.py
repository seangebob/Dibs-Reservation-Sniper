"""The shared, atomic mock booking state repository (in-memory)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.mock_booking import (
    InMemoryMockBookingStateRepository,
)
from backend.integrations.base import (
    ReconciliationStatus,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.models.reservation import (
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
)
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


def slot(slot_id: str, *, party_size: int = 4) -> AvailabilitySlot:
    return AvailabilitySlot(
        slot_id=slot_id,
        provider="mock",
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        date="2026-09-05",
        start_time="19:00",
        end_time=None,
        party_size=party_size,
        max_party_size=party_size,
    )


def confirm(target: AvailabilitySlot) -> BookingConfirmation:
    return BookingConfirmation(
        booking_id=f"mock_booking_{target.slot_id}",
        provider="mock",
        status=BookingStatus.MOCK_CONFIRMED,
        slot=target,
        created_at=NOW,
    )


def repo(
    *,
    capacity: int = 100,
    idle_ttl_seconds: float = 3600.0,
    retention_seconds: float = WEEK.total_seconds(),
) -> InMemoryMockBookingStateRepository:
    return InMemoryMockBookingStateRepository(
        capacity=capacity,
        idle_ttl_seconds=idle_ttl_seconds,
        retention_seconds=retention_seconds,
    )


def test_publish_admits_new_candidates_and_touches_repeats() -> None:
    async def scenario() -> None:
        store = repo()
        first = await store.publish_and_filter([slot("a"), slot("b")], "op1", NOW)
        assert {s.slot_id for s in first} == {"a", "b"}

        # A later search of the same candidates returns them again (touched).
        later = NOW + timedelta(seconds=30)
        again = await store.publish_and_filter([slot("a")], "op2", later)
        assert [s.slot_id for s in again] == ["a"]

    asyncio.run(scenario())


def test_a_booked_slot_is_never_republished_and_blocks_other_keys() -> None:
    async def scenario() -> None:
        store = repo()
        await store.publish_and_filter([slot("a")], "op1", NOW)
        booked = await store.book_slot("a", "watch:1", confirm, NOW)
        assert booked.status is BookingStatus.MOCK_CONFIRMED

        # A later search cannot resurrect the booked slot.
        republished = await store.publish_and_filter([slot("a")], "op2", NOW)
        assert republished == []

        # A different key cannot book the same slot.
        with pytest.raises(SlotUnavailableError):
            await store.book_slot("a", "watch:2", confirm, NOW)

    asyncio.run(scenario())


def test_booking_is_idempotent_for_the_same_key() -> None:
    async def scenario() -> None:
        store = repo()
        await store.publish_and_filter([slot("a")], "op1", NOW)
        first = await store.book_slot("a", "watch:1", confirm, NOW)
        second = await store.book_slot("a", "watch:1", confirm, NOW)
        assert second.booking_id == first.booking_id

    asyncio.run(scenario())


def test_booking_a_slot_that_was_never_published_is_not_found() -> None:
    async def scenario() -> None:
        store = repo()
        with pytest.raises(SlotNotFoundError):
            await store.book_slot("ghost", "watch:1", confirm, NOW)

    asyncio.run(scenario())


def test_reconcile_is_authoritative_confirmed_or_absent() -> None:
    async def scenario() -> None:
        store = repo()
        await store.publish_and_filter([slot("a")], "op1", NOW)

        absent = await store.reconcile_booking("watch:1", None, NOW)
        assert absent.status is ReconciliationStatus.DEFINITIVELY_ABSENT

        await store.book_slot("a", "watch:1", confirm, NOW)
        present = await store.reconcile_booking("watch:1", None, NOW)
        assert present.status is ReconciliationStatus.CONFIRMED
        assert present.confirmation is not None

    asyncio.run(scenario())


def test_capacity_omits_new_candidates_when_everything_is_pinned() -> None:
    async def scenario() -> None:
        store = repo(capacity=1)
        # op1 admits and pins "a".
        assert [s.slot_id for s in await store.publish_and_filter(
            [slot("a")], "op1", NOW
        )] == ["a"]
        # op2 cannot admit "b": capacity is one and "a" is pinned, so "b" is
        # deterministically omitted rather than evicting a pin.
        assert await store.publish_and_filter([slot("b")], "op2", NOW) == []

    asyncio.run(scenario())


def test_capacity_evicts_the_oldest_unpinned_slot_under_pressure() -> None:
    async def scenario() -> None:
        store = repo(capacity=1)
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.release_operation("op1")  # "a" is now unpinned

        # With "a" unpinned, publishing "b" evicts "a" to stay within capacity.
        later = NOW + timedelta(seconds=1)
        assert [s.slot_id for s in await store.publish_and_filter(
            [slot("b")], "op2", later
        )] == ["b"]
        # "a" is gone: booking it is now not found.
        with pytest.raises(SlotNotFoundError):
            await store.book_slot("a", "watch:1", confirm, later)

    asyncio.run(scenario())


def test_a_query_larger_than_capacity_is_truncated_deterministically() -> None:
    async def scenario() -> None:
        store = repo(capacity=2)
        admitted = await store.publish_and_filter(
            [slot("a"), slot("b"), slot("c")], "op1", NOW
        )
        # In candidate order, only the first two fit.
        assert [s.slot_id for s in admitted] == ["a", "b"]

    asyncio.run(scenario())


def test_cleanup_evicts_idle_unbooked_slots_past_the_ttl() -> None:
    async def scenario() -> None:
        store = repo(idle_ttl_seconds=60.0)
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.release_operation("op1")

        # Before the TTL nothing is removed; after it, the idle slot goes.
        early = await store.cleanup(NOW + timedelta(seconds=30), batch_size=10)
        assert early.idle_slots == 0
        late = await store.cleanup(NOW + timedelta(seconds=90), batch_size=10)
        assert late.idle_slots == 1

    asyncio.run(scenario())


def test_cleanup_leaves_a_pinned_slot_alone() -> None:
    async def scenario() -> None:
        store = repo(idle_ttl_seconds=60.0)
        await store.publish_and_filter([slot("a")], "op1", NOW)  # pinned ~120s

        result = await store.cleanup(NOW + timedelta(seconds=90), batch_size=10)
        assert result.idle_slots == 0  # still pinned by op1

    asyncio.run(scenario())


def test_a_booking_survives_until_its_retention_then_cleans_up() -> None:
    async def scenario() -> None:
        store = repo(retention_seconds=WEEK.total_seconds())
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.book_slot("a", "watch:1", confirm, NOW)

        # Within the week the replay record is intact.
        assert await store.get_booking("watch:1", NOW + timedelta(days=6)) is not None

        # Past the week the coordinated cleanup removes it.
        after = NOW + WEEK + timedelta(seconds=1)
        counts = await store.cleanup(after, batch_size=10)
        assert counts.expired_records == 1
        assert await store.get_booking("watch:1", after) is None

    asyncio.run(scenario())
