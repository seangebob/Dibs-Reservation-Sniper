"""`_attach_postgres`: every failure mode degrades to a disabled projection.

Exercised through the real `create_app()` + `TestClient` lifespan, matching
this repo's existing convention for `_attach_redis`
(`test_watch_recovery_wiring.py`) rather than importing the private function
directly. `create_pool`/`run_migrations` are monkeypatched at their
`backend.main` import site so no real network connection is attempted --
unlike Redis, there's no local-Docker fallback story to also exercise here,
since Postgres in this milestone is purely additive.
"""

from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.config import ConfigurationError
import backend.main as main_module
from backend.main import create_app


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("POSTGRES_URL", "POSTGRES_POOL_MIN_SIZE", "POSTGRES_POOL_MAX_SIZE"):
        monkeypatch.delenv(var, raising=False)


def test_no_postgres_url_leaves_history_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    app = create_app()

    with TestClient(app):
        pass

    assert app.state.watch_history is None
    assert app.state.postgres_pool is None


def test_a_malformed_postgres_url_leaves_history_disabled_not_startup_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "mysql://wrong-scheme/dibs")
    app = create_app()

    # Startup must not raise -- a bad POSTGRES_URL degrades the optional
    # history projection, it does not take down the whole application.
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert app.state.watch_history is None


def test_an_unreachable_postgres_leaves_history_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")

    async def failing_create_pool(settings: Any) -> Any:
        raise ConfigurationError("connection refused")

    monkeypatch.setattr(main_module, "create_pool", failing_create_pool)
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert app.state.watch_history is None
    assert app.state.postgres_pool is None


def test_a_failed_migration_leaves_history_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    fake_pool = _FakePool()

    async def fake_create_pool(settings: Any) -> Any:
        return fake_pool

    async def failing_run_migrations(pool: Any) -> list[str]:
        raise ConfigurationError("permission denied for schema public")

    monkeypatch.setattr(main_module, "create_pool", fake_create_pool)
    monkeypatch.setattr(main_module, "run_migrations", failing_run_migrations)
    app = create_app()

    with TestClient(app):
        pass

    assert app.state.watch_history is None


def test_a_reachable_postgres_wires_a_working_history_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    fake_pool = _FakePool()

    async def fake_create_pool(settings: Any) -> Any:
        return fake_pool

    async def fake_run_migrations(pool: Any) -> list[str]:
        return ["0001_watch_history"]

    monkeypatch.setattr(main_module, "create_pool", fake_create_pool)
    monkeypatch.setattr(main_module, "run_migrations", fake_run_migrations)
    app = create_app()

    with TestClient(app):
        assert app.state.watch_history is not None
        assert app.state.postgres_pool is fake_pool

    # Shutdown closes the pool, mirroring the Redis client's own lifecycle.
    assert fake_pool.closed is True
