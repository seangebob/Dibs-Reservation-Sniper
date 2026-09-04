"""Task 7: the Celery worker composes the same projection and notifier the API does.

Before Milestone 6 this worker had neither, so every poll outcome discovered in
the background updated no dashboard and told nobody -- while
`infra/docker-compose.yml` passed it `POSTGRES_URL` with the comment "Same
projection so background poll outcomes are recorded to history too". These tests
are what make that comment true.

No PostgreSQL, Redis, or broker is contacted: the pool factory is stubbed, which
is enough because the collaborators are built from settings and repositories.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

pytest.importorskip("celery", reason="requires the worker extra")
pytest.importorskip("kombu", reason="requires the worker extra")

from backend.config import Settings  # noqa: E402
from backend.db.repositories.watch_history import (  # noqa: E402
    WatchHistoryRepository,
)
from backend.integrations.email import EmailNotificationService  # noqa: E402
from backend.services.notification_service import (  # noqa: E402
    LoggingNotificationService,
)
from backend.workers.tasks import monitor_watch as task_module  # noqa: E402


_ENV = (
    "POSTGRES_URL",
    "SMTP_HOST",
    "SMTP_FROM",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_TIMEOUT_SECONDS",
    "DASHBOARD_BASE_URL",
)

#: `_runner` is included because a shutdown test closes it; without a reset the
#: next test would reuse a closed runner.
_CACHES = (
    task_module._settings,
    task_module._runner,
    task_module._redis_client,
    task_module._postgres_pool,
    task_module.build_watch_service,
)


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRedis:
    """Stands in for the Redis client, including the shutdown path."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch):
    """Clear every cache and env var this module touches, before and after."""

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    for cache in _CACHES:
        cache.cache_clear()
    monkeypatch.setattr(
        task_module, "_settings", lambda: Settings(openai_api_key="test-key")
    )
    # lru_cache-wrapped, because the shutdown path inspects `.cache_info()` to
    # avoid constructing a client purely in order to close it.
    monkeypatch.setattr(
        task_module, "_redis_client", lru_cache(maxsize=1)(_FakeRedis)
    )
    task_module._resources_closed = False
    yield
    for cache in _CACHES:
        cache.cache_clear()
    task_module._resources_closed = False


def _with_postgres(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Configure PostgreSQL and stub the pool so no server is contacted."""

    pool = _FakePool()
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql://dibs:dibs@localhost:5432/dibs"
    )

    async def _fake_create_pool(_settings: Any) -> Any:
        return pool

    monkeypatch.setattr(task_module, "create_pool", _fake_create_pool)
    return pool


def _with_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "dibs@example.com")


# --- Requirement 4.1 / 4.2: parity with the API process --------------------


def test_a_configured_worker_records_to_the_durable_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_postgres(monkeypatch)

    service = task_module.build_watch_service()

    assert isinstance(service._history, WatchHistoryRepository)


def test_a_configured_worker_composes_the_email_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_postgres(monkeypatch)
    _with_smtp(monkeypatch)

    service = task_module.build_watch_service()

    assert isinstance(service._notifier, EmailNotificationService)


def test_postgres_without_smtp_projects_but_stays_log_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_postgres(monkeypatch)

    service = task_module.build_watch_service()

    assert isinstance(service._history, WatchHistoryRepository)
    assert isinstance(service._notifier, LoggingNotificationService)


def test_smtp_without_postgres_stays_log_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no account to email without the projection, so SMTP alone
    cannot enable delivery."""

    _with_smtp(monkeypatch)

    service = task_module.build_watch_service()

    assert service._history is None
    assert isinstance(service._notifier, LoggingNotificationService)


# --- Requirement 4.3: unchanged without PostgreSQL -------------------------


def test_an_unconfigured_worker_behaves_exactly_as_before() -> None:
    service = task_module.build_watch_service()

    assert service._history is None
    assert isinstance(service._notifier, LoggingNotificationService)


def test_an_unreachable_database_degrades_rather_than_failing_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A worker that cannot reach PostgreSQL must still poll watches."""

    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql://dibs:dibs@localhost:5432/dibs"
    )

    async def _boom(_settings: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(task_module, "create_pool", _boom)

    with caplog.at_level("ERROR"):
        service = task_module.build_watch_service()

    assert service._history is None
    assert any("unreachable" in r.getMessage() for r in caplog.records)


def test_invalid_email_settings_do_not_cost_the_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_postgres(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")  # no SMTP_FROM

    service = task_module.build_watch_service()

    assert isinstance(service._history, WatchHistoryRepository)
    assert isinstance(service._notifier, LoggingNotificationService)


# --- Requirement 4.4: closed exactly once ----------------------------------


def test_shutdown_closes_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _with_postgres(monkeypatch)
    task_module.build_watch_service()

    task_module._close_worker_resources()

    assert pool.closed is True


def test_shutdown_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _with_postgres(monkeypatch)
    task_module.build_watch_service()

    task_module._close_worker_resources()
    task_module._close_worker_resources()  # Celery signal *and* atexit

    assert pool.closed is True


def test_shutdown_without_a_pool_builds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing must not construct a pool purely in order to close it."""

    built: list[str] = []

    async def _should_not_run(_settings: Any) -> Any:
        built.append("pool")
        return _FakePool()

    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql://dibs:dibs@localhost:5432/dibs"
    )
    monkeypatch.setattr(task_module, "create_pool", _should_not_run)

    task_module._close_worker_resources()

    assert built == []
