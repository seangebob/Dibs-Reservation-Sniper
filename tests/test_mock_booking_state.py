"""The shared, atomic mock booking state repository.

Every scenario runs against both the in-memory store and the exact Redis-Lua
store, so the two implementations must agree on capacity, eviction, pins,
booking protection, and cleanup for each case.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import fakeredis.aioredis as fakeredis_aio

from backend.db.repositories.mock_booking import (
    InMemoryMockBookingStateRepository,
    RedisMockBookingStateRepository,
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


@pytest.fixture(params=["memory", "redis"])
def make_store(request: pytest.FixtureRequest):
    """Build either store; both must satisfy the same contract."""

    def build(
        *,
        capacity: int = 100,
        idle_ttl_seconds: float = 3600.0,
        retention_seconds: float = WEEK.total_seconds(),
    ):  # noqa: ANN202
        if request.param == "memory":
            return InMemoryMockBookingStateRepository(
                capacity=capacity,
                idle_ttl_seconds=idle_ttl_seconds,
                retention_seconds=retention_seconds,
            )
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        return RedisMockBookingStateRepository(
            client,
            capacity=capacity,
            idle_ttl_seconds=idle_ttl_seconds,
            retention_seconds=retention_seconds,
        )

    return build


def test_publish_admits_new_candidates_and_touches_repeats(make_store) -> None:
    async def scenario() -> None:
        store = make_store()
        first = await store.publish_and_filter([slot("a"), slot("b")], "op1", NOW)
        assert {s.slot_id for s in first} == {"a", "b"}

        later = NOW + timedelta(seconds=30)
        again = await store.publish_and_filter([slot("a")], "op2", later)
        assert [s.slot_id for s in again] == ["a"]

    asyncio.run(scenario())


def test_a_booked_slot_is_never_republished_and_blocks_other_keys(
    make_store,
) -> None:
    async def scenario() -> None:
        store = make_store()
        await store.publish_and_filter([slot("a")], "op1", NOW)
        booked = await store.book_slot("a", "watch:1", confirm, NOW)
        assert booked.status is BookingStatus.MOCK_CONFIRMED

        republished = await store.publish_and_filter([slot("a")], "op2", NOW)
        assert republished == []

        with pytest.raises(SlotUnavailableError):
            await store.book_slot("a", "watch:2", confirm, NOW)

    asyncio.run(scenario())


def test_booking_is_idempotent_for_the_same_key(make_store) -> None:
    async def scenario() -> None:
        store = make_store()
        await store.publish_and_filter([slot("a")], "op1", NOW)
        first = await store.book_slot("a", "watch:1", confirm, NOW)
        second = await store.book_slot("a", "watch:1", confirm, NOW)
        assert second.booking_id == first.booking_id

    asyncio.run(scenario())


def test_booking_a_slot_that_was_never_published_is_not_found(make_store) -> None:
    async def scenario() -> None:
        store = make_store()
        with pytest.raises(SlotNotFoundError):
            await store.book_slot("ghost", "watch:1", confirm, NOW)

    asyncio.run(scenario())


def test_reconcile_is_authoritative_confirmed_or_absent(make_store) -> None:
    async def scenario() -> None:
        store = make_store()
        await store.publish_and_filter([slot("a")], "op1", NOW)

        absent = await store.reconcile_booking("watch:1", None, NOW)
        assert absent.status is ReconciliationStatus.DEFINITIVELY_ABSENT

        await store.book_slot("a", "watch:1", confirm, NOW)
        present = await store.reconcile_booking("watch:1", None, NOW)
        assert present.status is ReconciliationStatus.CONFIRMED
        assert present.confirmation is not None

    asyncio.run(scenario())


def test_capacity_omits_new_candidates_when_everything_is_pinned(
    make_store,
) -> None:
    async def scenario() -> None:
        store = make_store(capacity=1)
        admitted = await store.publish_and_filter([slot("a")], "op1", NOW)
        assert [s.slot_id for s in admitted] == ["a"]
        # Capacity is one and "a" is pinned by op1, so "b" is omitted.
        assert await store.publish_and_filter([slot("b")], "op2", NOW) == []

    asyncio.run(scenario())


def test_capacity_evicts_the_oldest_unpinned_slot_under_pressure(
    make_store,
) -> None:
    async def scenario() -> None:
        store = make_store(capacity=1)
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.release_operation("op1")

        later = NOW + timedelta(seconds=1)
        admitted = await store.publish_and_filter([slot("b")], "op2", later)
        assert [s.slot_id for s in admitted] == ["b"]
        with pytest.raises(SlotNotFoundError):
            await store.book_slot("a", "watch:1", confirm, later)

    asyncio.run(scenario())


def test_a_query_larger_than_capacity_is_truncated_deterministically(
    make_store,
) -> None:
    async def scenario() -> None:
        store = make_store(capacity=2)
        admitted = await store.publish_and_filter(
            [slot("a"), slot("b"), slot("c")], "op1", NOW
        )
        assert [s.slot_id for s in admitted] == ["a", "b"]

    asyncio.run(scenario())


def test_a_crash_expired_pin_stops_protecting_its_slot(make_store) -> None:
    async def scenario() -> None:
        store = make_store(capacity=1)
        await store.publish_and_filter([slot("a")], "op1", NOW)
        # op1 never releases (a crash). After the pin lifetime lapses, "a" is
        # evictable again, so a later search can admit a new slot.
        after_pin = NOW + timedelta(seconds=200)
        admitted = await store.publish_and_filter([slot("b")], "op2", after_pin)
        assert [s.slot_id for s in admitted] == ["b"]

    asyncio.run(scenario())


def test_cleanup_evicts_idle_unbooked_slots_past_the_ttl(make_store) -> None:
    async def scenario() -> None:
        store = make_store(idle_ttl_seconds=60.0)
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.release_operation("op1")

        early = await store.cleanup(NOW + timedelta(seconds=30), batch_size=10)
        assert early.idle_slots == 0
        late = await store.cleanup(NOW + timedelta(seconds=90), batch_size=10)
        assert late.idle_slots == 1

    asyncio.run(scenario())


def test_cleanup_leaves_a_pinned_slot_alone(make_store) -> None:
    async def scenario() -> None:
        store = make_store(idle_ttl_seconds=60.0)
        await store.publish_and_filter([slot("a")], "op1", NOW)

        result = await store.cleanup(NOW + timedelta(seconds=90), batch_size=10)
        assert result.idle_slots == 0  # still pinned by op1 (~120s)

    asyncio.run(scenario())


def test_a_booking_survives_until_its_retention_then_cleans_up(make_store) -> None:
    async def scenario() -> None:
        store = make_store(retention_seconds=WEEK.total_seconds())
        await store.publish_and_filter([slot("a")], "op1", NOW)
        await store.book_slot("a", "watch:1", confirm, NOW)

        assert await store.get_booking("watch:1", NOW + timedelta(days=6)) is not None

        after = NOW + WEEK + timedelta(seconds=1)
        counts = await store.cleanup(after, batch_size=10)
        assert counts.expired_records == 1
        assert await store.get_booking("watch:1", after) is None

    asyncio.run(scenario())
