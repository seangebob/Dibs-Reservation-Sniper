"""Failure classification and resource handling for the Celery watch task.

The worker-only imports are guarded so an installation without the `worker`
extra still collects and runs the rest of the suite.
"""

import asyncio
from datetime import UTC, datetime
from functools import lru_cache
import logging
import threading

import pytest


pytest.importorskip("celery", reason="requires the worker extra")
pytest.importorskip("kombu", reason="requires the worker extra")

from celery.exceptions import Retry  # noqa: E402
from kombu.exceptions import OperationalError as BrokerOperationalError  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402
from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: E402
from redis.exceptions import TimeoutError as RedisTimeoutError  # noqa: E402

from backend.models.reservation import AvailabilityQuery  # noqa: E402
from backend.models.watch import (  # noqa: E402
    Watch,
    WatchPollOutcome,
    WatchPollResult,
    WatchStatus,
)
from backend.orchestrator.schemas import VenueType  # noqa: E402
from backend.workers.tasks import monitor_watch as task_module  # noqa: E402
from backend.workers.tasks.monitor_watch import monitor_watch  # noqa: E402


def _watch() -> Watch:
    """A minimal ACTIVE watch; the poll result only needs a valid record."""

    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Watch(
        watch_id="watch_fixture",
        status=WatchStatus.ACTIVE,
        query=AvailabilityQuery(
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo-Cambridge, ON",
            party_size=4,
            date="2026-09-05",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        ),
        created_at=now,
        updated_at=now,
        expires_at=datetime(2026, 9, 6, 4, 0, tzinfo=UTC),
        attempts=0,
        max_attempts=200,
    )


def _result(
    outcome: WatchPollOutcome,
    retry_in_seconds: float | None = None,
) -> WatchPollResult:
    """Build the one valid result shape for each outcome."""

    watch = None if outcome is WatchPollOutcome.UNKNOWN_WATCH else _watch()
    return WatchPollResult(
        outcome=outcome,
        watch=watch,
        retry_in_seconds=retry_in_seconds,
    )


class _Probe(BaseModel):
    """Only used to obtain a genuine Pydantic ValidationError instance."""

    count: int


def _validation_error() -> ValidationError:
    try:
        _Probe(count="not-an-int")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


class CustomWatchFailure(Exception):
    """A domain exception that is not an infrastructure failure."""


#: Exceptions that must never be retried. None is an instance of the
#: recoverable redis-py / Kombu tuple, so each must escape by identity.
NON_RECOVERABLE = [
    pytest.param(RuntimeError("boom"), id="runtime-error"),
    pytest.param(TypeError("bad argument"), id="type-error"),
    pytest.param(_validation_error(), id="pydantic-validation-error"),
    pytest.param(ConnectionError("builtin connection"), id="builtin-connection-error"),
    pytest.param(TimeoutError("builtin timeout"), id="builtin-timeout-error"),
    pytest.param(CustomWatchFailure("domain failure"), id="custom-exception"),
]


class PollServiceDouble:
    """Stands in for `WatchService` without Redis, Celery, or a provider."""

    def __init__(
        self,
        *,
        raises: BaseException | None = None,
        result: WatchPollResult | None = None,
    ) -> None:
        self._raises = raises
        self._result = result
        self.calls: list[str] = []
        self.window_calls: list[tuple[str, str]] = []

    async def poll_once(self, watch_id: str) -> WatchPollResult:
        self.calls.append(watch_id)
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result

    async def poll_window(
        self,
        watch_id: str,
        window_id: str,
        *,
        owner_id: str | None = None,
        enforce_due: bool = True,
    ) -> WatchPollResult:
        self.window_calls.append((watch_id, window_id))
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


class RunnerDouble:
    """Runs the coroutine synchronously and records every execution."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, coro):  # noqa: ANN001, ANN201
        self.runs += 1
        return asyncio.run(coro)


class RetrySpy:
    """Records `self.retry(...)` calls and returns Celery's Retry signal."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, exc=None, countdown=None, **kwargs):  # noqa: ANN001, ANN204
        self.calls.append({"exc": exc, "countdown": countdown, **kwargs})
        return Retry("retry requested")


#: Cached factories captured before any test patches the module globals.
_CACHED_FACTORIES = (
    task_module._settings,
    task_module._runner,
    task_module._redis_client,
    task_module.build_watch_service,
)


@pytest.fixture(autouse=True)
def isolated_task_module():
    """Restore module globals, caches, and the resource flag after each case.

    This fixture deliberately does not depend on `monkeypatch`: pytest tears
    fixtures down in reverse dependency order, so requesting it here would run
    this cleanup while the patched globals were still in place.
    """

    originals = {
        name: getattr(task_module, name)
        for name in ("_settings", "_runner", "_redis_client", "build_watch_service")
    }
    task_module._resources_closed = False
    yield
    for name, value in originals.items():
        setattr(task_module, name, value)
    for factory in _CACHED_FACTORIES:
        factory.cache_clear()
    task_module._resources_closed = False


@pytest.fixture
def retry_spy(monkeypatch: pytest.MonkeyPatch) -> RetrySpy:
    spy = RetrySpy()
    monkeypatch.setattr(monitor_watch, "retry", spy)
    return spy


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    service: PollServiceDouble,
    runner: RunnerDouble,
) -> None:
    monkeypatch.setattr(task_module, "build_watch_service", lambda: service)
    monkeypatch.setattr(task_module, "_runner", lambda: runner)


def _retry_failure_logged(caplog: pytest.LogCaptureFixture) -> bool:
    return any(
        "monitor_watch failed" in record.getMessage() for record in caplog.records
    )


# --------------------------------------------------------------------------
# Property 1: Bug Condition - failure-class boundaries in the worker
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failure", NON_RECOVERABLE)
def test_non_recoverable_failures_escape_without_retry(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C_worker(X): a failure outside the redis/Kombu tuple must propagate."""

    recoverable = (RedisConnectionError, RedisTimeoutError, BrokerOperationalError)
    assert not isinstance(failure, recoverable)

    service = PollServiceDouble(raises=failure)
    runner = RunnerDouble()
    _bind(monkeypatch, service, runner)

    with caplog.at_level(logging.ERROR, logger=task_module.__name__):
        with pytest.raises(type(failure)) as caught:
            monitor_watch.run("watch_boundary")

    assert caught.value is failure
    assert retry_spy.calls == []
    assert not _retry_failure_logged(caplog)


def test_closed_resources_invariant_escapes_without_running_or_retrying(
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The `_resources_closed` guard is a programming invariant, not an outage."""

    service = PollServiceDouble(result=_result(WatchPollOutcome.UNKNOWN_WATCH))
    runner = RunnerDouble()
    _bind(monkeypatch, service, runner)
    monkeypatch.setattr(task_module, "_resources_closed", True)

    with caplog.at_level(logging.ERROR, logger=task_module.__name__):
        with pytest.raises(RuntimeError, match="already closed"):
            monitor_watch.run("watch_closed")

    assert runner.runs == 0
    assert service.calls == []
    assert retry_spy.calls == []
    assert not _retry_failure_logged(caplog)


# --------------------------------------------------------------------------
# Property 2: Preservation - recoverable infrastructure retry contract
# --------------------------------------------------------------------------


RECOVERABLE = [
    pytest.param(RedisConnectionError("redis refused"), id="redis-connection-error"),
    pytest.param(RedisTimeoutError("redis timed out"), id="redis-timeout-error"),
    pytest.param(BrokerOperationalError("broker down"), id="kombu-operational-error"),
]


@pytest.mark.parametrize("failure", RECOVERABLE)
def test_recoverable_infrastructure_failures_retry_with_the_original_exception(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Baseline contract observed on the current code, and kept by the fix."""

    service = PollServiceDouble(raises=failure)
    _bind(monkeypatch, service, RunnerDouble())

    with caplog.at_level(logging.ERROR, logger=task_module.__name__):
        with pytest.raises(Retry):
            monitor_watch.run("watch_infra")

    assert len(retry_spy.calls) == 1
    assert retry_spy.calls[0]["exc"] is failure
    assert retry_spy.calls[0]["countdown"] == 60

    failures = [
        record
        for record in caplog.records
        if "monitor_watch failed" in record.getMessage()
    ]
    assert len(failures) == 1
    # logger.exception, not logger.error: the traceback must survive.
    assert failures[0].exc_info is not None


def test_the_task_keeps_three_retries() -> None:
    assert monitor_watch.max_retries == 3


SUCCESSFUL_RESULTS = [
    pytest.param(WatchPollOutcome.NO_AVAILABILITY, 150.0, id="rescheduled-min-jitter"),
    pytest.param(WatchPollOutcome.NO_AVAILABILITY, 210.0, id="rescheduled-max-jitter"),
    pytest.param(WatchPollOutcome.FOUND, None, id="found"),
    pytest.param(WatchPollOutcome.BOOKED, None, id="booked"),
    pytest.param(WatchPollOutcome.EXPIRED, None, id="expired"),
    pytest.param(WatchPollOutcome.ALREADY_FINISHED, None, id="already-finished"),
    pytest.param(WatchPollOutcome.UNKNOWN_WATCH, None, id="unknown-watch"),
]


@pytest.mark.parametrize(("outcome", "retry_in_seconds"), SUCCESSFUL_RESULTS)
def test_a_successful_poll_returns_the_exact_result_shape(
    outcome: WatchPollOutcome,
    retry_in_seconds: float | None,
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = PollServiceDouble(result=_result(outcome, retry_in_seconds))
    _bind(monkeypatch, service, RunnerDouble())

    with caplog.at_level(logging.ERROR, logger=task_module.__name__):
        returned = monitor_watch.run("watch_success")

    assert returned == {
        "watch_id": "watch_success",
        "outcome": outcome.value,
        "retry_in_seconds": retry_in_seconds,
    }
    assert isinstance(returned["outcome"], str)
    assert retry_spy.calls == []
    assert not _retry_failure_logged(caplog)


def test_a_window_argument_takes_the_window_aware_service_path(
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A two-argument delivery polls the exact window; the result shape is kept."""

    service = PollServiceDouble(
        result=_result(WatchPollOutcome.NO_AVAILABILITY, 180.0)
    )
    _bind(monkeypatch, service, RunnerDouble())

    with caplog.at_level(logging.ERROR, logger=task_module.__name__):
        returned = monitor_watch.run("watch_win", "watch_win:3")

    assert service.window_calls == [("watch_win", "watch_win:3")]
    assert service.calls == []  # the legacy poll_once path was not taken
    assert returned == {
        "watch_id": "watch_win",
        "outcome": "NO_AVAILABILITY",
        "retry_in_seconds": 180.0,
    }
    assert retry_spy.calls == []
    assert not _retry_failure_logged(caplog)


def test_a_one_argument_delivery_still_resolves_the_current_window(
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
) -> None:
    """An already-queued one-argument job keeps using `poll_once`."""

    service = PollServiceDouble(result=_result(WatchPollOutcome.FOUND))
    _bind(monkeypatch, service, RunnerDouble())

    monitor_watch.run("watch_legacy")

    assert service.calls == ["watch_legacy"]
    assert service.window_calls == []
    assert retry_spy.calls == []


def test_the_runner_lock_admits_only_one_concurrent_poll(
    monkeypatch: pytest.MonkeyPatch,
    retry_spy: RetrySpy,
) -> None:
    """Two threads must not enter `_runner().run(...)` at the same time.

    The barrier is the proof: if both threads were ever inside the runner
    together they would meet at it. Serialization breaks it instead.
    """

    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    state = {"current": 0, "max": 0, "met": False}

    class SerializationRunner:
        def run(self, coro):  # noqa: ANN001, ANN202
            coro.close()
            with counter_lock:
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
            try:
                barrier.wait(timeout=0.25)
                state["met"] = True
            except threading.BrokenBarrierError:
                pass
            with counter_lock:
                state["current"] -= 1
            return _result(WatchPollOutcome.FOUND)

    service = PollServiceDouble(result=_result(WatchPollOutcome.FOUND))
    _bind(monkeypatch, service, SerializationRunner())

    threads = [
        threading.Thread(target=monitor_watch.run, args=(f"watch_{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert state["max"] == 1
    assert state["met"] is False
    assert retry_spy.calls == []


# --------------------------------------------------------------------------
# Property 2: Preservation - lazy, ordered, idempotent resource cleanup
# --------------------------------------------------------------------------


class CleanupRunner:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def run(self, coro):  # noqa: ANN001, ANN202
        return asyncio.run(coro)

    def close(self) -> None:
        self.events.append("runner-close")


class CleanupRedisClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def aclose(self) -> None:
        self.events.append("redis-aclose")


def _install_caches(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    constructions: list[str],
):  # noqa: ANN202
    """Replace the module's cached factories with recording equivalents."""

    @lru_cache(maxsize=1)
    def runner_factory() -> CleanupRunner:
        constructions.append("runner")
        return CleanupRunner(events)

    @lru_cache(maxsize=1)
    def redis_factory() -> CleanupRedisClient:
        constructions.append("redis")
        return CleanupRedisClient(events)

    monkeypatch.setattr(task_module, "_runner", runner_factory)
    monkeypatch.setattr(task_module, "_redis_client", redis_factory)
    return runner_factory, redis_factory


def test_cleanup_constructs_nothing_when_no_resource_was_ever_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    constructions: list[str] = []
    _install_caches(monkeypatch, events, constructions)

    task_module._close_worker_resources()

    assert constructions == []
    assert events == []
    assert task_module._resources_closed is True


def test_cleanup_closes_redis_before_the_runner_that_runs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    constructions: list[str] = []
    runner_factory, redis_factory = _install_caches(monkeypatch, events, constructions)
    runner_factory()
    redis_factory()

    task_module._close_worker_resources()

    assert events == ["redis-aclose", "runner-close"]


def test_cleanup_skips_redis_when_only_the_runner_was_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    constructions: list[str] = []
    runner_factory, _ = _install_caches(monkeypatch, events, constructions)
    runner_factory()

    task_module._close_worker_resources()

    assert constructions == ["runner"]
    assert events == ["runner-close"]


def test_cleanup_is_idempotent_across_repeated_shutdown_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    constructions: list[str] = []
    runner_factory, redis_factory = _install_caches(monkeypatch, events, constructions)
    runner_factory()
    redis_factory()

    task_module._close_worker_resources()
    task_module._close_worker_resources()
    task_module._close_on_worker_process_shutdown()

    assert events == ["redis-aclose", "runner-close"]
