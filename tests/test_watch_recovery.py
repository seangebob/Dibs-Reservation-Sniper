"""Startup and follow-up reconciliation of persisted watch schedules.

Every scenario runs the coordinator against a real dispatcher and either
repository. Memory mode coordinates process-locally; Redis mode elects a leader
over a finite lease and prunes stale index members the in-memory store cannot
hold. The two must agree on classification, dispatch, expiry, synthesis, and
bounded cleanup.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import fakeredis.aioredis as fakeredis_aio

from backend.db.repositories.watches import (
    ACTIVE_INDEX_KEY,
    INDEX_KEY,
    SCHEDULE_INDEX_KEY,
    InMemoryWatchRepository,
    RedisWatchRepository,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import initial_runtime, window_id_for
from backend.orchestrator.schemas import VenueType
from backend.services.watch_recovery import RecoveryCoordinator
from backend.workers.dispatcher import WatchScheduleDispatcher


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 9, 6, tzinfo=UTC)
HORIZON = 300.0
LEASE = 120.0
EARLIEST_DELAY = 150.0


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        window_id=None,
        delay_seconds=0.0,
        due_at=None,
        task_id=None,
    ) -> None:
        self.calls.append({"watch_id": watch_id, "window_id": window_id})


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
    watch_id: str,
    *,
    next_check_at: datetime = NOW,
    attempts: int = 0,
    max_attempts: int = 25_000,
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


async def _seed(repo, watch: Watch) -> None:
    runtime = initial_runtime(
        watch, required_attempts=2593, supports_deadline=True
    )
    await repo.create_with_schedule(watch, runtime)


class Harness:
    """A repository, a dispatcher over one queue, and a coordinator on one clock."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.clock = Clock(NOW)
        if kind == "memory":
            self.client = None
            self.repo = InMemoryWatchRepository(clock=self.clock)
            distributed = False
        else:
            self.client = fakeredis_aio.FakeRedis(decode_responses=True)
            self.repo = RedisWatchRepository(self.client, clock=self.clock)
            distributed = True
        self.queue = RecordingQueue()
        self.dispatcher = WatchScheduleDispatcher(
            self.repo,
            self.queue,
            owner_id="rec-a",
            horizon_seconds=HORIZON,
            lease_seconds=30.0,
            recovery_grace_seconds=60.0,
            clock=self.clock,
        )
        self.coordinator = RecoveryCoordinator(
            self.repo,
            self.dispatcher,
            owner_id="rec-a",
            distributed=distributed,
            leader_lease_seconds=30.0,
            earliest_delay_seconds=EARLIEST_DELAY,
            clock=self.clock,
        )

    async def drop_marker(self, watch_id: str) -> None:
        if self.kind == "memory":
            self.repo._markers.pop(watch_id, None)
        else:
            await self.client.zrem(SCHEDULE_INDEX_KEY, watch_id)

    async def marker_present(self, watch_id: str) -> bool:
        return await self.repo.schedule_marker(watch_id) is not None


@pytest.fixture(params=["memory", "redis"])
def harness(request: pytest.FixtureRequest) -> Harness:
    return Harness(request.param)


def test_a_due_active_window_is_dispatched(harness: Harness) -> None:
    async def scenario() -> None:
        await _seed(harness.repo, _watch("watch_1", next_check_at=NOW))

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.is_leader is True
        assert outcome.considered == 1
        assert outcome.dispatched == 1
        assert [c["watch_id"] for c in harness.queue.calls] == ["watch_1"]
        assert outcome.ready is True

    asyncio.run(scenario())


def test_a_far_future_active_window_is_preserved_not_dispatched(
    harness: Harness,
) -> None:
    async def scenario() -> None:
        await _seed(
            harness.repo,
            _watch("watch_far", next_check_at=NOW + timedelta(hours=1)),
        )

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.dispatched == 0
        assert harness.queue.calls == []
        # The durable marker is retained for a later sweep as the horizon nears.
        assert await harness.marker_present("watch_far") is True
        assert outcome.ready is True

    asyncio.run(scenario())


def test_an_attempt_exhausted_active_watch_is_expired_not_dispatched(
    harness: Harness,
) -> None:
    async def scenario() -> None:
        await _seed(
            harness.repo,
            _watch("watch_done", attempts=25_000, max_attempts=25_000),
        )

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.expired == 1
        assert outcome.dispatched == 0
        assert harness.queue.calls == []
        stored = await harness.repo.get("watch_done")
        assert stored is not None and stored.status is WatchStatus.EXPIRED

    asyncio.run(scenario())


def test_an_overdue_active_watch_is_expired(harness: Harness) -> None:
    async def scenario() -> None:
        # Its calendar deadline has already passed at NOW.
        await _seed(
            harness.repo,
            _watch(
                "watch_late",
                next_check_at=NOW - timedelta(hours=2),
                expires_at=NOW - timedelta(minutes=1),
            ),
        )

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.expired == 1
        stored = await harness.repo.get("watch_late")
        assert stored is not None and stored.status is WatchStatus.EXPIRED

    asyncio.run(scenario())


def test_an_active_record_missing_its_marker_gets_one_synthesized(
    harness: Harness,
) -> None:
    async def scenario() -> None:
        await _seed(harness.repo, _watch("watch_1", next_check_at=NOW))
        await harness.drop_marker("watch_1")
        assert await harness.marker_present("watch_1") is False

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.synthesized == 1
        assert await harness.marker_present("watch_1") is True
        # Synthesized and due, so the same pass dispatches it.
        assert outcome.dispatched == 1

    asyncio.run(scenario())


def test_a_live_poll_claim_defers_marker_synthesis(harness: Harness) -> None:
    async def scenario() -> None:
        await _seed(harness.repo, _watch("watch_1", next_check_at=NOW))
        await harness.drop_marker("watch_1")
        claim = await harness.repo.claim_window(
            "watch_1", window_id_for("watch_1", 0), "worker-x", LEASE
        )
        assert claim.claim is not None  # a worker is mid-flight on the window

        outcome = await harness.coordinator.reconcile_once()

        # The owner will commit the next marker; recovery must not race it.
        assert outcome.synthesized == 0
        assert await harness.marker_present("watch_1") is False

    asyncio.run(scenario())


def test_recovery_runs_bounded_terminal_cleanup(harness: Harness) -> None:
    async def scenario() -> None:
        await _seed(harness.repo, _watch("watch_gone", next_check_at=NOW))
        await harness.repo.cancel_if_active("watch_gone")  # terminal + retention
        harness.clock.advance(604_800 + 1)  # past the one-week retention

        outcome = await harness.coordinator.reconcile_once()

        assert outcome.cleanup_removed == 1
        assert await harness.repo.get("watch_gone") is None

    asyncio.run(scenario())


# -- Redis-only: stale index members the in-memory store cannot hold ---------


def test_a_missing_document_active_member_is_pruned() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client, clock=clock)
        # An active-index member whose document never landed (or was TTL'd away).
        await client.sadd(ACTIVE_INDEX_KEY, "watch_ghost")
        await client.sadd(INDEX_KEY, "watch_ghost")
        coordinator = _redis_coordinator(repo, client, clock)

        outcome = await coordinator.reconcile_once()

        assert outcome.pruned == 1
        assert await client.sismember(ACTIVE_INDEX_KEY, "watch_ghost") == 0
        assert await client.sismember(INDEX_KEY, "watch_ghost") == 0

    asyncio.run(scenario())


def test_a_corrupt_document_active_member_is_pruned() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client, clock=clock)
        await client.set("dibs:watch:watch_bad", '{"not":"a watch"}')
        await client.sadd(ACTIVE_INDEX_KEY, "watch_bad")
        await client.sadd(INDEX_KEY, "watch_bad")
        coordinator = _redis_coordinator(repo, client, clock)

        outcome = await coordinator.reconcile_once()

        assert outcome.pruned == 1
        assert await client.sismember(ACTIVE_INDEX_KEY, "watch_bad") == 0

    asyncio.run(scenario())


def test_a_terminal_document_in_the_active_index_is_pruned_from_active_only() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client, clock=clock)
        await _seed(repo, _watch("watch_fin", next_check_at=NOW))
        await repo.cancel_if_active("watch_fin")  # terminal; leaves active index
        # Simulate a stale active-index membership left by an older path.
        await client.sadd(ACTIVE_INDEX_KEY, "watch_fin")
        coordinator = _redis_coordinator(repo, client, clock)

        outcome = await coordinator.reconcile_once()

        assert outcome.pruned == 1
        assert await client.sismember(ACTIVE_INDEX_KEY, "watch_fin") == 0
        # Retention keeps the terminal document readable in the all index.
        assert await client.sismember(INDEX_KEY, "watch_fin") == 1
        assert await repo.get("watch_fin") is not None

    asyncio.run(scenario())


def test_two_coordinators_over_one_redis_elect_a_single_leader() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client, clock=clock)
        await _seed(repo, _watch("watch_1", next_check_at=NOW))

        first = _redis_coordinator(repo, client, clock, owner_id="rec-a")
        second = _redis_coordinator(repo, client, clock, owner_id="rec-b")

        outcome_a = await first.reconcile_once()
        outcome_b = await second.reconcile_once()

        assert outcome_a.is_leader is True
        assert outcome_b.is_leader is False
        # Only the leader scanned, so the window is dispatched exactly once.
        assert outcome_a.dispatched == 1
        assert outcome_b.dispatched == 0

    asyncio.run(scenario())


def test_another_coordinator_resumes_after_the_leader_releases() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client, clock=clock)

        first = _redis_coordinator(repo, client, clock, owner_id="rec-a")
        second = _redis_coordinator(repo, client, clock, owner_id="rec-b")

        assert (await first.reconcile_once()).is_leader is True
        assert (await second.reconcile_once()).is_leader is False
        await first.release()  # the leader shuts down cleanly

        assert (await second.reconcile_once()).is_leader is True

    asyncio.run(scenario())


class _ExpireBomb:
    """Wraps a repository so one watch's expiry raises, the rest succeed."""

    def __init__(self, inner, bad_watch_id: str) -> None:
        self._inner = inner
        self._bad = bad_watch_id

    async def expire_if_eligible(self, watch_id: str, **kwargs: object):
        if watch_id == self._bad:
            raise RuntimeError("injected expiry failure")
        return await self._inner.expire_if_eligible(watch_id, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def test_a_failing_candidate_does_not_abort_the_remaining_ones() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(
            repo, _watch("watch_bad", attempts=25_000, max_attempts=25_000)
        )
        await _seed(repo, _watch("watch_ok", next_check_at=NOW))
        queue = RecordingQueue()
        dispatcher = WatchScheduleDispatcher(
            repo,
            queue,
            owner_id="rec-a",
            horizon_seconds=HORIZON,
            clock=clock,
        )
        coordinator = RecoveryCoordinator(
            _ExpireBomb(repo, "watch_bad"),
            dispatcher,
            owner_id="rec-a",
            distributed=False,
            leader_lease_seconds=30.0,
            earliest_delay_seconds=EARLIEST_DELAY,
            clock=clock,
        )

        outcome = await coordinator.reconcile_once()

        assert outcome.failed == 1
        assert outcome.backlog is True
        # The healthy watch was still reached and dispatched despite the earlier
        # failure; the un-expired bad watch stays due for a later retry.
        dispatched_ids = {c["watch_id"] for c in queue.calls}
        assert "watch_ok" in dispatched_ids

    asyncio.run(scenario())


def _redis_coordinator(
    repo, client, clock, *, owner_id: str = "rec-a"
) -> RecoveryCoordinator:
    queue = RecordingQueue()
    dispatcher = WatchScheduleDispatcher(
        repo,
        queue,
        owner_id=owner_id,
        horizon_seconds=HORIZON,
        clock=clock,
    )
    return RecoveryCoordinator(
        repo,
        dispatcher,
        owner_id=owner_id,
        distributed=True,
        leader_lease_seconds=30.0,
        earliest_delay_seconds=EARLIEST_DELAY,
        clock=clock,
    )
