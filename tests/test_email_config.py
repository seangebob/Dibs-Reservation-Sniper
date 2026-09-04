"""`EmailSettings`: disabled by default, coherent once enabled, secret-safe.

Mirrors `test_account_config.py`: bounds are validated at startup so a bad value
is a deploy-time error rather than a delivery failure at 2am. The privacy tests
at the bottom cover Requirement 6.1/6.2 -- neither the SMTP password nor a
reservation's details may reach a log line.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from backend.config import (
    DEFAULT_DASHBOARD_BASE_URL,
    DEFAULT_SMTP_PORT,
    DEFAULT_SMTP_TIMEOUT_SECONDS,
    ConfigurationError,
    EmailSettings,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.notification_service import (
    LoggingNotificationService,
    WatchEvent,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

#: Every SMTP variable, so one test can clear them all and start from "unset".
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


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "dibs@example.com")


# --- disabled by default ---------------------------------------------------


def test_no_smtp_host_means_disabled() -> None:
    settings = EmailSettings.from_environment()

    assert settings.enabled is False
    assert settings.host is None


def test_a_blank_smtp_host_is_also_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "   ")

    assert EmailSettings.from_environment().enabled is False


def test_defaults_are_documented(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)

    settings = EmailSettings.from_environment()

    assert settings.enabled is True
    assert settings.port == DEFAULT_SMTP_PORT == 587
    assert settings.timeout_seconds == DEFAULT_SMTP_TIMEOUT_SECONDS == 10
    assert settings.starttls is True
    assert settings.dashboard_base_url == DEFAULT_DASHBOARD_BASE_URL


# --- coherence once enabled ------------------------------------------------


def test_a_host_without_a_sender_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    with pytest.raises(ConfigurationError, match="SMTP_FROM"):
        EmailSettings.from_environment()


def test_a_username_without_a_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "dibs")

    with pytest.raises(ConfigurationError, match="SMTP_PASSWORD"):
        EmailSettings.from_environment()


def test_an_unauthenticated_relay_needs_neither_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)

    settings = EmailSettings.from_environment()

    assert settings.username is None and settings.password is None


def test_an_out_of_range_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "70000")

    with pytest.raises(ConfigurationError):
        EmailSettings.from_environment()


def test_an_out_of_range_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "600")

    with pytest.raises(ConfigurationError):
        EmailSettings.from_environment()


@pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("no", False),
                                          ("true", True), ("1", True), ("on", True)])
def test_starttls_accepts_the_usual_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_STARTTLS", raw)

    assert EmailSettings.from_environment().starttls is expected


def test_an_ambiguous_starttls_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo must not silently downgrade a connection carrying credentials."""

    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_STARTTLS", "ture")

    with pytest.raises(ConfigurationError, match="boolean"):
        EmailSettings.from_environment()


def test_a_malformed_dashboard_url_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "not-a-url")

    with pytest.raises(ConfigurationError, match="DASHBOARD_BASE_URL"):
        EmailSettings.from_environment()


def test_a_dashboard_url_loses_its_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dibs.example.com/")

    assert (
        EmailSettings.from_environment().dashboard_base_url
        == "https://dibs.example.com"
    )


# --- Requirement 6.2: the password never shows up --------------------------


def test_the_password_is_absent_from_every_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "dibs")
    monkeypatch.setenv("SMTP_PASSWORD", "s3cr3t-sentinel")

    settings = EmailSettings.from_environment()

    assert settings.password == "s3cr3t-sentinel"  # readable by the sender
    assert "s3cr3t-sentinel" not in repr(settings)
    assert "s3cr3t-sentinel" not in str(settings)


# --- Requirement 6.1: no reservation details in the log --------------------


def _watch() -> Watch:
    return Watch(
        watch_id="watch_1",
        status=WatchStatus.ACTIVE,
        query=AvailabilityQuery(
            venue_name="Bhima's Warung",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo-Cambridge, ON",
            party_size=7,
            date="2026-09-05",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        ),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=3,
        max_attempts=10,
        next_check_at=NOW,
    )


def test_the_log_line_carries_no_reservation_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        asyncio.new_event_loop().run_until_complete(
            LoggingNotificationService().notify(
                _watch(), WatchEvent.AVAILABILITY_FOUND
            )
        )

    rendered = " ".join(record.getMessage() for record in caplog.records)
    # What operating the system actually needs:
    assert "watch_1" in rendered
    assert "AVAILABILITY_FOUND" in rendered
    assert "attempts=3" in rendered
    # ...and what it does not (someone's reservation):
    for private in ("Bhima", "2026-09-05", "party=7", "19:00"):
        assert private not in rendered
