"""`WatchHistoryRepository`: upsert-on-outcome projection, against a fake pool.

No live PostgreSQL is started. The fake connection replicates just enough of
the migration's `ON CONFLICT ... COALESCE` upsert semantics to prove the one
behavior that actually matters here: a later call with no owner (a poll
outcome, which carries no client identity) must never erase an owner recorded
at creation. Everything else is plain dict bookkeeping.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from backend.db.repositories.watch_history import WatchHistoryRepository
from backend.models.reservation import (
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def query(venue_name: str = "Cote") -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name=venue_name,
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def watch(
    watch_id: str = "watch_1",
    *,
    status: WatchStatus = WatchStatus.ACTIVE,
    created_at: datetime = NOW,
    updated_at: datetime | None = None,
    booking: BookingConfirmation | None = None,
    found_slots: list[AvailabilitySlot] | None = None,
) -> Watch:
    return Watch(
        watch_id=watch_id,
        status=status,
        query=query(),
        auto_book=False,
        created_at=created_at,
        updated_at=updated_at or created_at,
        expires_at=created_at + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=created_at if status is WatchStatus.ACTIVE else None,
        found_slots=found_slots or [],
        booking=booking,
    )


def _slot(slot_id: str = "slot_1") -> AvailabilitySlot:
    return AvailabilitySlot(
        slot_id=slot_id,
        provider="mock",
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        date="2026-09-05",
        start_time="19:00",
        end_time="21:00",
        party_size=4,
        max_party_size=4,
    )


def _booking(booking_id: str = "booking_1") -> BookingConfirmation:
    return BookingConfirmation(
        booking_id=booking_id,
        provider="mock",
        status=BookingStatus.MOCK_CONFIRMED,
        slot=_slot(),
        created_at=NOW,
    )


class _FakeConnection:
    """Replicates the migration's upsert semantics well enough to test them."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))
        if query.startswith("UPDATE watch_history SET user_id"):
            owner_client_id, user_id = args
            for row in self.rows.values():
                if row["owner_client_id"] == owner_client_id and row["user_id"] is None:
                    row["user_id"] = user_id
            return
        assert "INSERT INTO watch_history" in query
        (
            watch_id,
            owner_client_id,
            status,
            created_at,
            updated_at,
            expires_at,
            watch_json,
            user_id,
        ) = args
        existing = self.rows.get(watch_id)
        if existing is not None:
            # Mirrors ON CONFLICT ... col = COALESCE(EXCLUDED.col, existing):
            # an ownerless later write must not erase a recorded owner.
            if owner_client_id is None:
                owner_client_id = existing["owner_client_id"]
            if user_id is None:
                user_id = existing["user_id"]
        self.rows[watch_id] = {
            "watch_id": watch_id,
            "owner_client_id": owner_client_id,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
            "expires_at": expires_at,
            "watch_json": watch_json,
            "user_id": user_id,
        }

    async def fetchval(self, query: str, *args: Any) -> Any:
        raise NotImplementedError

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "WHERE watch_id = " in query:
            (watch_id,) = args
            row = self.rows.get(watch_id)
            return [row] if row is not None else []
        if "WHERE owner_client_id = " in query:
            owner_client_id, limit = args
            matches = [
                row for row in self.rows.values()
                if row["owner_client_id"] == owner_client_id
            ]
            matches.sort(key=lambda row: row["updated_at"], reverse=True)
            return matches[:limit]
        if "WHERE user_id = " in query:
            user_id, limit = args
            matches = [
                row for row in self.rows.values() if row["user_id"] == user_id
            ]
            matches.sort(key=lambda row: row["updated_at"], reverse=True)
            return matches[:limit]
        raise AssertionError(f"unexpected fetch query: {query}")


class _AcquireCM:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return self._pool.connection

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self) -> _AcquireCM:
        return _AcquireCM(self)

    async def close(self) -> None:
        pass


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------


def test_recording_a_new_watch_makes_it_readable_by_id() -> None:
    repo = WatchHistoryRepository(_FakePool())
    original = watch()

    _run(repo.record(original, owner_client_id="visitor-1"))
    stored = _run(repo.get("watch_1"))

    assert stored == original


def test_getting_an_unknown_watch_id_returns_none() -> None:
    repo = WatchHistoryRepository(_FakePool())

    assert _run(repo.get("watch_ghost")) is None


def test_recording_the_same_watch_twice_updates_the_projection_in_place() -> None:
    repo = WatchHistoryRepository(_FakePool())
    created = watch(status=WatchStatus.ACTIVE)
    later = watch(
        status=WatchStatus.FOUND,
        updated_at=NOW + timedelta(minutes=5),
        found_slots=[_slot()],
    )

    _run(repo.record(created, owner_client_id="visitor-1"))
    _run(repo.record(later, owner_client_id="visitor-1"))
    stored = _run(repo.get("watch_1"))

    assert stored == later
    assert stored.status is WatchStatus.FOUND


def test_a_later_record_with_no_owner_does_not_erase_the_recorded_owner() -> None:
    """The scenario Task 4 depends on: a poll outcome carries no client id."""

    repo = WatchHistoryRepository(_FakePool())
    created = watch(status=WatchStatus.ACTIVE)
    polled = watch(status=WatchStatus.ACTIVE, updated_at=NOW + timedelta(minutes=3))

    _run(repo.record(created, owner_client_id="visitor-1"))
    _run(repo.record(polled, owner_client_id=None))

    owned = _run(repo.list_for_owner("visitor-1"))
    assert [w.watch_id for w in owned] == ["watch_1"]


def test_a_later_record_with_a_real_owner_does_overwrite_the_recorded_owner() -> None:
    repo = WatchHistoryRepository(_FakePool())
    created = watch(status=WatchStatus.ACTIVE)
    reassigned = watch(status=WatchStatus.ACTIVE, updated_at=NOW + timedelta(minutes=3))

    _run(repo.record(created, owner_client_id="visitor-1"))
    _run(repo.record(reassigned, owner_client_id="visitor-2"))

    assert [w.watch_id for w in _run(repo.list_for_owner("visitor-1"))] == []
    assert [w.watch_id for w in _run(repo.list_for_owner("visitor-2"))] == ["watch_1"]


def test_recording_with_no_owner_at_all_leaves_the_watch_unowned() -> None:
    repo = WatchHistoryRepository(_FakePool())

    _run(repo.record(watch(), owner_client_id=None))

    assert _run(repo.get("watch_1")) is not None
    assert _run(repo.list_for_owner("anyone")) == []


def test_list_for_owner_returns_most_recently_updated_first() -> None:
    repo = WatchHistoryRepository(_FakePool())
    older = watch(watch_id="watch_old", updated_at=NOW)
    newer = watch(watch_id="watch_new", updated_at=NOW + timedelta(minutes=10))

    _run(repo.record(older, owner_client_id="visitor-1"))
    _run(repo.record(newer, owner_client_id="visitor-1"))

    result = _run(repo.list_for_owner("visitor-1"))
    assert [w.watch_id for w in result] == ["watch_new", "watch_old"]


def test_list_for_owner_never_returns_another_owners_watch() -> None:
    repo = WatchHistoryRepository(_FakePool())
    _run(repo.record(watch(watch_id="watch_mine"), owner_client_id="visitor-1"))
    _run(repo.record(watch(watch_id="watch_theirs"), owner_client_id="visitor-2"))

    result = _run(repo.list_for_owner("visitor-1"))
    assert [w.watch_id for w in result] == ["watch_mine"]


def test_list_for_owner_respects_the_limit() -> None:
    repo = WatchHistoryRepository(_FakePool())
    for i in range(5):
        _run(
            repo.record(
                watch(watch_id=f"watch_{i}", updated_at=NOW + timedelta(minutes=i)),
                owner_client_id="visitor-1",
            )
        )

    result = _run(repo.list_for_owner("visitor-1", limit=2))
    assert [w.watch_id for w in result] == ["watch_4", "watch_3"]


def test_list_for_owner_rejects_a_non_positive_limit() -> None:
    repo = WatchHistoryRepository(_FakePool())

    with pytest.raises(ValueError, match="limit must be between"):
        _run(repo.list_for_owner("visitor-1", limit=0))


def test_list_for_owner_rejects_a_limit_above_the_ceiling() -> None:
    repo = WatchHistoryRepository(_FakePool())

    with pytest.raises(ValueError, match="limit must be between"):
        _run(repo.list_for_owner("visitor-1", limit=1001))


# ---------------------------------------------------------------------------
# Milestone 5: account ownership via user_id (Requirements 3.1-3.3).
# ---------------------------------------------------------------------------


def test_recording_with_a_user_id_makes_the_watch_account_owned() -> None:
    repo = WatchHistoryRepository(_FakePool())
    user_id = uuid4()

    _run(repo.record(watch(), owner_client_id="visitor-1", user_id=user_id))

    assert _run(repo.get_account_owner("watch_1")) == user_id
    assert [w.watch_id for w in _run(repo.list_for_user(user_id))] == ["watch_1"]


def test_get_account_owner_is_none_for_anonymous_or_unknown_watches() -> None:
    repo = WatchHistoryRepository(_FakePool())
    _run(repo.record(watch(), owner_client_id="visitor-1"))  # anonymous

    assert _run(repo.get_account_owner("watch_1")) is None
    assert _run(repo.get_account_owner("watch_ghost")) is None


def test_a_later_ownerless_record_does_not_erase_the_account_owner() -> None:
    """A poll outcome carries no user id; it must not unassign the account."""

    repo = WatchHistoryRepository(_FakePool())
    user_id = uuid4()
    _run(repo.record(watch(status=WatchStatus.ACTIVE), user_id=user_id))
    _run(
        repo.record(
            watch(status=WatchStatus.ACTIVE, updated_at=NOW + timedelta(minutes=3))
        )
    )

    assert _run(repo.get_account_owner("watch_1")) == user_id


def test_list_for_user_never_returns_another_accounts_watch() -> None:
    repo = WatchHistoryRepository(_FakePool())
    mine, theirs = uuid4(), uuid4()
    _run(repo.record(watch(watch_id="watch_mine"), user_id=mine))
    _run(repo.record(watch(watch_id="watch_theirs"), user_id=theirs))

    assert [w.watch_id for w in _run(repo.list_for_user(mine))] == ["watch_mine"]


def test_claim_anonymous_assigns_the_clients_unclaimed_watches() -> None:
    repo = WatchHistoryRepository(_FakePool())
    user_id = uuid4()
    _run(repo.record(watch(watch_id="w1"), owner_client_id="visitor-1"))
    _run(repo.record(watch(watch_id="w2"), owner_client_id="visitor-1"))
    _run(repo.record(watch(watch_id="w3"), owner_client_id="visitor-2"))  # not theirs

    _run(repo.claim_anonymous("visitor-1", user_id))

    owned = {w.watch_id for w in _run(repo.list_for_user(user_id))}
    assert owned == {"w1", "w2"}
    assert _run(repo.get_account_owner("w3")) is None


def test_claim_anonymous_never_steals_an_already_claimed_watch() -> None:
    repo = WatchHistoryRepository(_FakePool())
    first, second = uuid4(), uuid4()
    _run(repo.record(watch(watch_id="w1"), owner_client_id="visitor-1", user_id=first))

    # A different account logs in from the same reused client id: the guard
    # (user_id IS NULL) means it claims nothing already owned (Req 4.4).
    _run(repo.claim_anonymous("visitor-1", second))

    assert _run(repo.get_account_owner("w1")) == first


def test_claim_anonymous_is_idempotent() -> None:
    repo = WatchHistoryRepository(_FakePool())
    user_id = uuid4()
    _run(repo.record(watch(watch_id="w1"), owner_client_id="visitor-1"))

    _run(repo.claim_anonymous("visitor-1", user_id))
    _run(repo.claim_anonymous("visitor-1", user_id))  # second call: no change

    assert _run(repo.get_account_owner("w1")) == user_id


def test_round_trip_preserves_a_booked_watchs_full_shape() -> None:
    """Requirement 3.3: the durable projection must reflect the exact public
    shape, including a real booking confirmation and its nested slot."""

    repo = WatchHistoryRepository(_FakePool())
    booked = watch(
        status=WatchStatus.BOOKED,
        found_slots=[_slot()],
        booking=_booking(),
    )

    _run(repo.record(booked, owner_client_id="visitor-1"))
    stored = _run(repo.get("watch_1"))

    assert stored == booked
    assert stored.booking is not None
    assert stored.booking.slot.slot_id == "slot_1"
