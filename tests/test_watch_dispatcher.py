"""The schedule-marker dispatcher: horizon, single-flight, and crash safety."""

import asyncio
from datetime import UTC, datetime, timedelta

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import initial_runtime, window_id_for
from backend.orchestrator.schemas import VenueType
from backend.workers.dispatcher import WatchScheduleDispatcher


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 9, 6, tzinfo=UTC)
HORIZON = 300.0


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingQueue:
    """Captures the keyword contract every publication carries."""

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
        self.calls.append(
            {
                "watch_id": watch_id,
                "window_id": window_id,
                "delay_seconds": delay_seconds,
                "due_at": due_at,
                "task_id": task_id,
            }
        )


class FailingQueue:
    def __init__(self) -> None:
        self.attempts = 0

    async def enqueue_watch_poll(self, *args: object, **kwargs: object) -> None:
        self.attempts += 1
        raise RuntimeError("broker unavailable")


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


def _watch(watch_id: str, *, next_check_at: datetime) -> Watch:
    return Watch(
        watch_id=watch_id,
        status=WatchStatus.ACTIVE,
        query=_query(),
        created_at=NOW,
        updated_at=NOW,
        expires_at=EXPIRES,
        attempts=0,
        max_attempts=25_000,
        next_check_at=next_check_at,
    )


async def _seed(repo: InMemoryWatchRepository, watch: Watch) -> None:
    runtime = initial_runtime(
        watch, required_attempts=2593, supports_deadline=True
    )
    await repo.create_with_schedule(watch, runtime)


def _dispatcher(repo, queue, clock, **kwargs) -> WatchScheduleDispatcher:  # noqa: ANN001
    params = {
        "owner_id": "disp-a",
        "horizon_seconds": HORIZON,
        "lease_seconds": 30.0,
        "recovery_grace_seconds": 60.0,
        "clock": clock,
    }
    params.update(kwargs)
    return WatchScheduleDispatcher(repo, queue, **params)


def test_a_due_marker_is_published_with_its_window_and_zero_delay() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(repo, _watch("watch_1", next_check_at=NOW))
        queue = RecordingQueue()

        result = await _dispatcher(repo, queue, clock).dispatch_due()

        assert result.dispatched == 1
        assert len(queue.calls) == 1
        call = queue.calls[0]
        assert call["watch_id"] == "watch_1"
        assert call["window_id"] == window_id_for("watch_1", 0)
        assert call["delay_seconds"] == 0.0
        assert call["due_at"] == NOW
        assert call["task_id"] == f"dibs-poll:{window_id_for('watch_1', 0)}:1"

    asyncio.run(scenario())


def test_a_within_horizon_future_marker_carries_the_remaining_delay() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        due = NOW + timedelta(seconds=180)
        await _seed(repo, _watch("watch_1", next_check_at=due))
        queue = RecordingQueue()

        await _dispatcher(repo, queue, clock).dispatch_due()

        assert queue.calls[0]["delay_seconds"] == 180.0
        assert queue.calls[0]["due_at"] == due

    asyncio.run(scenario())


def test_a_far_future_marker_is_left_durable_and_undispatched() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(
            repo, _watch("watch_far", next_check_at=NOW + timedelta(hours=1))
        )
        queue = RecordingQueue()

        result = await _dispatcher(repo, queue, clock).dispatch_due()

        assert result.considered == 0
        assert result.dispatched == 0
        assert queue.calls == []
        # The marker is still there for a later sweep as the horizon approaches.
        assert await repo.schedule_marker("watch_far") is not None

    asyncio.run(scenario())


def test_a_dispatched_marker_is_not_republished_within_the_grace() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(repo, _watch("watch_1", next_check_at=NOW))
        queue = RecordingQueue()
        dispatcher = _dispatcher(repo, queue, clock)

        first = await dispatcher.dispatch_due()
        # A second sweep a few seconds later (lease still short) must not
        # publish the same marker again: acceptance deferred it to the grace.
        clock.advance(5)
        second = await dispatcher.dispatch_due()

        assert first.dispatched == 1
        assert second.dispatched == 0
        assert second.deferred == 1
        assert len(queue.calls) == 1

    asyncio.run(scenario())


def test_a_publish_failure_releases_the_lease_and_reports_backlog() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(repo, _watch("watch_1", next_check_at=NOW))
        failing = FailingQueue()

        result = await _dispatcher(repo, failing, clock).dispatch_due()

        assert result.failed == 1
        assert result.dispatched == 0
        assert result.has_backlog is True

        # The lease was released, so a healthy dispatcher redispatches at once.
        recovered = RecordingQueue()
        again = await _dispatcher(repo, recovered, clock).dispatch_due()
        assert again.dispatched == 1
        assert len(recovered.calls) == 1

    asyncio.run(scenario())


def test_two_dispatchers_over_one_repo_publish_a_marker_once() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        await _seed(repo, _watch("watch_1", next_check_at=NOW))
        queue_a = RecordingQueue()
        queue_b = RecordingQueue()

        result_a = await _dispatcher(
            repo, queue_a, clock, owner_id="disp-a"
        ).dispatch_due()
        result_b = await _dispatcher(
            repo, queue_b, clock, owner_id="disp-b"
        ).dispatch_due()

        assert result_a.dispatched == 1
        assert result_b.dispatched == 0
        assert result_b.deferred == 1
        assert len(queue_a.calls) + len(queue_b.calls) == 1

    asyncio.run(scenario())


def test_the_batch_size_bounds_one_pass() -> None:
    async def scenario() -> None:
        clock = Clock(NOW)
        repo = InMemoryWatchRepository(clock=clock)
        for index in range(5):
            await _seed(repo, _watch(f"watch_{index}", next_check_at=NOW))
        queue = RecordingQueue()

        result = await _dispatcher(
            repo, queue, clock, batch_size=2
        ).dispatch_due()

        assert result.considered == 2
        assert result.dispatched == 2
        assert len(queue.calls) == 2

    asyncio.run(scenario())