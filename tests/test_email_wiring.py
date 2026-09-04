"""Task 6: composing the email notifier into the API process.

`notifier=` was a wiring point that did not exist at all before Milestone 6 --
`WatchService` always fell back to its logging default. These tests cover the
composition rules rather than delivery itself: what gets built, what does not,
and that a misconfiguration degrades instead of failing startup.

No PostgreSQL and no SMTP server: `_attach_postgres` is driven directly with a
fake pool, which is enough because the notifier is built from settings and
repositories, not from a live connection.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.config import DEFAULT_NOTIFY_TIMEOUT_SECONDS
from backend.integrations.email import EmailNotificationService
from backend.main import create_app
from backend.services.notification_service import LoggingNotificationService


_SMTP_VARS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_STARTTLS",
    "SMTP_TIMEOUT_SECONDS",
    "DASHBOARD_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SMTP_VARS:
        monkeypatch.delenv(name, raising=False)


class _FakePool:
    """Enough of a pool for repository construction; never queried here."""

    async def close(self) -> None:
        return None


def _enable_smtp(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "dibs@example.com")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def _attach(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run `_attach_postgres` against a fake pool, skipping the real connect."""

    import backend.main as main_module

    app = create_app()
    monkeypatch.setenv("POSTGRES_URL", "postgresql://dibs:dibs@localhost:5432/dibs")

    async def _fake_pool(_settings: Any) -> Any:
        return _FakePool()

    async def _no_migrations(_pool: Any) -> list[str]:
        return []

    monkeypatch.setattr(main_module, "create_pool", _fake_pool)
    monkeypatch.setattr(main_module, "run_migrations", _no_migrations)
    asyncio.new_event_loop().run_until_complete(main_module._attach_postgres(app))
    return app


# --- what gets built -------------------------------------------------------


def test_no_smtp_configuration_leaves_the_notifier_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5.1: unchanged Milestone 1-5 behavior."""

    app = _attach(monkeypatch)

    assert app.state.notifier is None
    assert app.state.notify_timeout_seconds == DEFAULT_NOTIFY_TIMEOUT_SECONDS


def test_configured_smtp_builds_the_email_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_smtp(monkeypatch)

    app = _attach(monkeypatch)

    assert isinstance(app.state.notifier, EmailNotificationService)


def test_the_notify_timeout_follows_the_transport_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither ceiling may silently pre-empt the other."""

    _enable_smtp(monkeypatch, SMTP_TIMEOUT_SECONDS="30")

    app = _attach(monkeypatch)

    assert app.state.notify_timeout_seconds == 30.0


def test_invalid_email_settings_degrade_instead_of_failing_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement 5.2: a half-configured mailer must not take the API down."""

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")  # no SMTP_FROM

    with caplog.at_level("ERROR"):
        app = _attach(monkeypatch)

    assert app.state.notifier is None  # log-only, as before
    assert any("Email settings invalid" in r.getMessage() for r in caplog.records)


def test_a_broken_mailer_does_not_disable_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email is built after accounts, so its failure must not take them with it."""

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")  # invalid: no sender

    app = _attach(monkeypatch)

    assert app.state.auth_service is not None
    assert app.state.watch_history is not None


def test_the_smtp_password_never_reaches_the_startup_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement 6.2."""

    _enable_smtp(
        monkeypatch, SMTP_USERNAME="dibs", SMTP_PASSWORD="s3cr3t-sentinel"
    )

    with caplog.at_level("DEBUG"):
        _attach(monkeypatch)

    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert "s3cr3t-sentinel" not in rendered


# --- what the watch service receives ---------------------------------------


def test_the_watch_service_defaults_to_logging_without_a_notifier() -> None:
    """create_app alone (no PostgreSQL, no SMTP) is the Milestone 1-5 path."""

    app = create_app()

    assert app.state.notifier is None
    assert isinstance(
        app.state.watch_service._notifier, LoggingNotificationService
    )


def test_the_built_watch_service_carries_the_configured_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The builder feeds every call site, so wiring it once covers them all."""

    import backend.main as main_module

    _enable_smtp(monkeypatch)
    app = _attach(monkeypatch)

    service = main_module._build_watch_service(
        app, repository=app.state.watch_service._repository
    )

    assert isinstance(service._notifier, EmailNotificationService)
    assert service._notify_timeout_seconds == app.state.notify_timeout_seconds
