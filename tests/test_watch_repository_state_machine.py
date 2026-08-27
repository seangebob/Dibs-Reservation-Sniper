"""The atomic watch repository: single-flight claims and fenced commits.

These exercise the in-memory implementation directly. The same trace-based
oracle comparison is reused against the exact Redis-Lua implementation in the
next phase, so both stores must satisfy one fenced single-flight model.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watch_decisions import (
    BookingPermitStatus,
    ClaimStatus,
    CommitStatus,
    CreateStatus,
    TransitionStatus,
)
from backend.db.repositories.watches import (
    InMemoryWatchRepository,
    RedisWatchRepository,
    terminal_event_id,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import initial_runtime, window_id_for
from backend.orchestrator.schemas import VenueType


import fakeredis.aioredis as fakeredis_aio  # noqa: E402


@pytest.fixture(params=["memory", "redis"])
def make_repo(request: pytest.FixtureRequest):
    """Build either repository over a shared injected clock.

    Every state-machine test runs against both stores, so the in-memory and
    exact Redis-Lua implementations must agree on every decision.
    """

    def build(clock: Clock) -> object:
        if request.param == "memory":
            return InMemoryWatchRepository(clock=clock)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        return RedisWatchRepository(client, clock=clock)

    return build


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 9, 6, tzinfo=UTC)
LEASE = 120.0


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def _watch(
    watch_id: str = "watch_1",
    *,
    attempts: int = 0,
    max_attempts: int = 25_000,
    next_check_at: datetime = NOW,
    expires_at: datetime = EXPIRES,
) -> Watch:
    return Watch(
        watch_id=watch_id,
        status=WatchStatus.ACTIVE,
        query=_query(),
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at,
        attempts=attempts,
        max_attempts=max_attempts,
        next_check_at=next_check_at,
    )


async def _create(repo: InMemoryWatchRepository, watch: Watch) -> None:
    runtime = initial_runtime(
        watch, required_attempts=2593, supports_deadline=True
    )
    result = await repo.create_with_schedule(watch, runtime)
    assert result.status is CreateStatus.CREATED


def _next_miss(claim, clock: Clock, *, delay: float = 180.0):  # noqa: ANN001, ANN202
    cadence = claim.runtime.cadence_sequence + 1
    scheduled = clock() + timedelta(seconds=delay)
    new_watch = claim.watch.model_copy(
        update={
            "attempts": claim.watch.attempts + 1,
            "last_checked_at": clock(),
            "updated_at": clock(),
            "next_check_at": scheduled,
        }
    )
    new_runtime = claim.runtime.model_copy(
        update={
            "cadence_sequence": cadence,
            "window_id": window_id_for(claim.watch.watch_id, cadence),
            "scheduled_for": scheduled,
            "consecutive_outages": 0,
        }
    )
    return new_watch, new_runtime


def _to_expired(claim, clock: Clock):  # noqa: ANN001, ANN202
    new_watch = claim.watch.model_copy(
        update={
            "status": WatchStatus.EXPIRED,
            "next_check_at": None,
            "updated_at": clock(),
        }
    )
    new_runtime = claim.runtime.model_copy(
        update={"window_id": None, "scheduled_for": None}
    )
    return new_watch, new_runtime


# --------------------------------------------------------------------------
# Preservation: creation, schedule marker, and legacy surface
# --------------------------------------------------------------------------


def test_create_persists_watch_runtime_and_a_due_marker(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)

        assert (await repo.get(watch.watch_id)) == watch
        runtime = await repo.get_runtime(watch.watch_id)
        assert runtime is not None and runtime.revision == 0
        marker = await repo.schedule_marker(watch.watch_id)
        assert marker is not None
        assert marker.window_id == window_id_for(watch.watch_id, 0)
        assert marker.scheduled_for == NOW

    asyncio.run(scenario())


def test_create_is_idempotent_on_a_duplicate_id(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        runtime = initial_runtime(
            watch, required_attempts=10, supports_deadline=True
        )

        result = await repo.create_with_schedule(watch, runtime)

        assert result.status is CreateStatus.ALREADY_EXISTS

    asyncio.run(scenario())


def test_legacy_list_and_delete_still_operate(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        await _create(repo, _watch("watch_1"))
        await _create(repo, _watch("watch_2"))

        assert {w.watch_id for w in await repo.list_all()} == {
            "watch_1",
            "watch_2",
        }
        assert len(await repo.list_active()) == 2
        assert await repo.delete("watch_1") is True
        assert await repo.get_runtime("watch_1") is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Property 3: single-flight, fenced, recoverable cadence windows
# --------------------------------------------------------------------------


def test_only_one_of_two_deliveries_claims_the_same_window(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        first = await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        second = await repo.claim_window(
            watch.watch_id, window, "owner-b", LEASE
        )

        assert first.status is ClaimStatus.OWNED
        assert second.status is ClaimStatus.BUSY

    asyncio.run(scenario())


def test_a_committed_window_makes_its_predecessor_stale(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        claim = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert claim is not None
        new_watch, new_runtime = _next_miss(claim, clock)
        commit = await repo.commit_window(claim, new_watch, new_runtime)
        assert commit.status is CommitStatus.COMMITTED

        # A duplicate delivery of the original window is now stale.
        redelivery = await repo.claim_window(
            watch.watch_id, window, "owner-b", LEASE
        )
        assert redelivery.status is ClaimStatus.STALE

    asyncio.run(scenario())


def test_a_stale_owner_cannot_commit_after_cancellation(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        claim = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert claim is not None

        # Cancellation wins while the owner is paused mid-poll.
        cancel = await repo.cancel_if_active(watch.watch_id)
        assert cancel.status is TransitionStatus.APPLIED

        new_watch, new_runtime = _next_miss(claim, clock)
        commit = await repo.commit_window(claim, new_watch, new_runtime)

        assert commit.status in {CommitStatus.FENCED, CommitStatus.TERMINAL}
        stored = await repo.get(watch.watch_id)
        assert stored is not None and stored.status is WatchStatus.CANCELLED

    asyncio.run(scenario())


def test_no_takeover_while_the_lease_is_unexpired(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        owned = await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        assert owned.status is ClaimStatus.OWNED

        clock.advance(LEASE - 1)  # still inside the lease
        busy = await repo.claim_window(watch.watch_id, window, "owner-b", LEASE)
        assert busy.status is ClaimStatus.BUSY

    asyncio.run(scenario())


def test_takeover_is_allowed_after_the_lease_expires(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        first = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert first is not None

        clock.advance(LEASE + 1)  # the crashed owner's lease has expired
        taken = await repo.claim_window(watch.watch_id, window, "owner-b", LEASE)
        assert taken.status is ClaimStatus.OWNED

        # The former owner can no longer commit its window.
        new_watch, new_runtime = _next_miss(first, clock)
        commit = await repo.commit_window(first, new_watch, new_runtime)
        assert commit.status is CommitStatus.FENCED

    asyncio.run(scenario())


def test_a_window_that_is_not_yet_due_is_early(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch(next_check_at=NOW + timedelta(seconds=180))
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        result = await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        assert result.status is ClaimStatus.EARLY

    asyncio.run(scenario())


def test_expiry_is_monotonic_and_a_stale_commit_cannot_reactivate(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch(attempts=2, max_attempts=3)
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)

        claim = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert claim is not None
        expired_watch, expired_runtime = _to_expired(claim, clock)
        commit = await repo.commit_window(claim, expired_watch, expired_runtime)
        assert commit.status is CommitStatus.COMMITTED
        assert commit.event_id == terminal_event_id(
            watch.watch_id, WatchStatus.EXPIRED, 1
        )

        # Any later claim sees a terminal watch.
        again = await repo.claim_window(
            watch.watch_id, window, "owner-b", LEASE
        )
        assert again.status is ClaimStatus.TERMINAL

    asyncio.run(scenario())


def test_claiming_an_unknown_watch_reports_unknown(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        result = await repo.claim_window("watch_missing", "w:0", "o", LEASE)
        assert result.status is ClaimStatus.UNKNOWN

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Booking permit and cancellation race
# --------------------------------------------------------------------------


def test_begin_booking_grants_a_permit_to_the_owner(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)
        claim = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert claim is not None

        permit = await repo.begin_booking(claim)
        assert permit.status is BookingPermitStatus.GRANTED
        assert permit.permit_id is not None

    asyncio.run(scenario())


def test_cancellation_after_a_permit_is_recorded_not_applied(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch()
        await _create(repo, watch)
        window = window_id_for(watch.watch_id, 0)
        claim = (
            await repo.claim_window(watch.watch_id, window, "owner-a", LEASE)
        ).claim
        assert claim is not None
        await repo.begin_booking(claim)

        cancel = await repo.cancel_if_active(watch.watch_id)

        # The booking is in flight, so cancellation is pending, not applied.
        assert cancel.status is TransitionStatus.NOT_ELIGIBLE
        runtime = await repo.get_runtime(watch.watch_id)
        assert runtime is not None and runtime.cancel_requested is True
        stored = await repo.get(watch.watch_id)
        assert stored is not None and stored.status is WatchStatus.ACTIVE

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Conditional expiry
# --------------------------------------------------------------------------


def test_expire_if_eligible_expires_an_exhausted_watch(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch(attempts=3, max_attempts=3)
        await _create(repo, watch)

        result = await repo.expire_if_eligible(watch.watch_id)

        assert result.status is TransitionStatus.APPLIED
        assert result.watch is not None
        assert result.watch.status is WatchStatus.EXPIRED

    asyncio.run(scenario())


def test_expire_if_eligible_leaves_a_healthy_watch_active(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch(attempts=0, max_attempts=3)
        await _create(repo, watch)

        result = await repo.expire_if_eligible(watch.watch_id)

        assert result.status is TransitionStatus.NOT_ELIGIBLE

    asyncio.run(scenario())


def test_expire_if_eligible_fences_on_a_revision_mismatch(make_repo) -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = make_repo(clock)
        watch = _watch(attempts=3, max_attempts=3)
        await _create(repo, watch)

        result = await repo.expire_if_eligible(
            watch.watch_id, expected_revision=99
        )

        assert result.status is TransitionStatus.FENCED

    asyncio.run(scenario())
