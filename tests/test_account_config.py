"""AccountSettings bounded validation (Milestone 5, Task 2)."""

import pytest

from backend.config import (
    DEFAULT_ARGON2_MEMORY_COST_KIB,
    DEFAULT_ARGON2_TIME_COST,
    DEFAULT_PASSWORD_MIN_LENGTH,
    DEFAULT_SESSION_TTL_SECONDS,
    AccountSettings,
    ConfigurationError,
)


_ACCOUNT_ENV = (
    "SESSION_TTL_SECONDS",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "ARGON2_TIME_COST",
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_PARALLELISM",
    "LOGIN_THROTTLE_MAX_ATTEMPTS",
    "LOGIN_THROTTLE_WINDOW_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ACCOUNT_ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_apply_when_nothing_is_set() -> None:
    settings = AccountSettings.from_environment()

    assert settings.session_ttl_seconds == DEFAULT_SESSION_TTL_SECONDS
    assert settings.password_min_length == DEFAULT_PASSWORD_MIN_LENGTH
    assert settings.argon2_time_cost == DEFAULT_ARGON2_TIME_COST
    assert settings.argon2_memory_cost_kib == DEFAULT_ARGON2_MEMORY_COST_KIB


def test_env_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_TTL_SECONDS", "3600")
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "12")

    settings = AccountSettings.from_environment()

    assert settings.session_ttl_seconds == 3600
    assert settings.password_min_length == 12


def test_out_of_range_session_ttl_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_TTL_SECONDS", "60")  # below the 1-hour floor
    with pytest.raises(ConfigurationError):
        AccountSettings.from_environment()


def test_non_integer_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "eight")
    with pytest.raises(ConfigurationError):
        AccountSettings.from_environment()


def test_password_max_below_min_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "20")
    monkeypatch.setenv("PASSWORD_MAX_LENGTH", "10")
    with pytest.raises(ConfigurationError, match="at least PASSWORD_MIN_LENGTH"):
        AccountSettings.from_environment()
