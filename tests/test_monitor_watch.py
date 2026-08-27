"""Failure classification and resource handling for the Celery watch task.

The worker-only imports are guarded so an installation without the `worker`
extra still collects and runs the rest of the suite.
"""

import asyncio
import logging

import pytest


pytest.importorskip("celery", reason="requires the worker extra")
pytest.importorskip("kombu", reason="requires the worker extra")

from celery.exceptions import Retry  # noqa: E402
from kombu.exceptions import OperationalError as BrokerOperationalError  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402
from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: E402
from redis.exceptions import TimeoutError as RedisTimeoutError  # noqa: E402

from backend.models.watch import WatchPollOutcome, WatchPollResult  # noqa: E402
from backend.workers.tasks import monitor_watch as task_module  # noqa: E402
from backend.workers.tasks.monitor_watch import monitor_watch  # noqa: E402


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

    async def poll_once(self, watch_id: str) -> WatchPollResult:
        self.calls.append(watch_id)
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

    service = PollServiceDouble(
        result=WatchPollResult(outcome=WatchPollOutcome.UNKNOWN_WATCH, watch=None)
    )
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
