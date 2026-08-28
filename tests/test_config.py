import pytest

from backend.config import (
    DEFAULT_MODEL,
    ConfigurationError,
    Settings,
    WatchSettings,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "RESERVATION_TIMEZONE",
        "REDIS_URL",
        "WATCH_POLL_INTERVAL_SECONDS",
        "WATCH_POLL_JITTER_SECONDS",
        "WATCH_MAX_POLL_ATTEMPTS",
        "WATCH_DISPATCH_HORIZON_SECONDS",
        "WATCH_PROVIDER_CALL_TIMEOUT_SECONDS",
        "WATCH_PROVIDER_BACKOFF_MAX_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_apply_when_only_the_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-test  ")

    settings = Settings.from_environment()

    assert settings.openai_api_key == "sk-test"
    assert settings.openai_model == DEFAULT_MODEL
    assert settings.timezone_name == "America/Toronto"


@pytest.mark.parametrize("value", ["", "   "])
def test_missing_api_key_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", value)

    with pytest.raises(ConfigurationError) as error:
        Settings.from_environment()

    assert "OPENAI_API_KEY" in str(error.value)
    assert "environment" in str(error.value)


def test_absent_api_key_is_reported_the_same_way() -> None:
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        Settings.from_environment()


@pytest.mark.parametrize(
    "model",
    ["", "   ", "gpt 5.6", "gpt-5.6; rm -rf /", "-leading-dash", "x", "a" * 65],
)
def test_invalid_model_names_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", model)

    with pytest.raises(ConfigurationError, match="OPENAI_MODEL"):
        Settings.from_environment()


@pytest.mark.parametrize(
    "model",
    ["gpt-5.6", "gpt-4.1-mini", "claude-opus-5", "ft:gpt-5.6:acme:tuned"],
)
def test_plausible_model_names_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", model)

    assert Settings.from_environment().openai_model == model


def test_unknown_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("RESERVATION_TIMEZONE", "Mars/Olympus_Mons")

    with pytest.raises(ConfigurationError, match="RESERVATION_TIMEZONE"):
        Settings.from_environment()


def test_alternate_timezone_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("RESERVATION_TIMEZONE", "UTC")

    assert Settings.from_environment().timezone_name == "UTC"


# --- watch attempt safety ceiling (milestone 3) -----------------------------


def test_the_default_attempt_ceiling_is_the_raised_safety_bound() -> None:
    """The default is a lifetime-covering ceiling, not the old fixed 200."""

    assert WatchSettings().max_poll_attempts == 25_000
    assert WatchSettings.from_environment().max_poll_attempts == 25_000


@pytest.mark.parametrize("value", ["1", "999", "25000", "1000000"])
def test_attempt_ceilings_within_bounds_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_MAX_POLL_ATTEMPTS", value)

    assert WatchSettings.from_environment().max_poll_attempts == int(value)


def test_a_zero_attempt_ceiling_is_still_rejected_as_non_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_MAX_POLL_ATTEMPTS", "0")

    with pytest.raises(ConfigurationError, match="must be a positive integer"):
        WatchSettings.from_environment()


@pytest.mark.parametrize("value", ["many", "5.5", "+5", "-5", "1_000"])
def test_non_integer_attempt_text_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_MAX_POLL_ATTEMPTS", value)

    with pytest.raises(ConfigurationError, match="must be an integer"):
        WatchSettings.from_environment()


def test_an_attempt_ceiling_above_the_upper_bound_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_MAX_POLL_ATTEMPTS", "1000001")

    with pytest.raises(ConfigurationError, match="WATCH_MAX_POLL_ATTEMPTS"):
        WatchSettings.from_environment()


def test_overlong_attempt_text_is_rejected_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_MAX_POLL_ATTEMPTS", "1" * 40)

    with pytest.raises(ConfigurationError, match="WATCH_MAX_POLL_ATTEMPTS"):
        WatchSettings.from_environment()


# --- dispatch horizon (milestone 3) -----------------------------------------


def test_the_default_dispatch_horizon_is_five_minutes() -> None:
    assert WatchSettings().dispatch_horizon_seconds == 300
    assert WatchSettings.from_environment().dispatch_horizon_seconds == 300


@pytest.mark.parametrize("value", ["30", "300", "3600"])
def test_dispatch_horizons_within_bounds_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_DISPATCH_HORIZON_SECONDS", value)

    assert WatchSettings.from_environment().dispatch_horizon_seconds == int(value)


@pytest.mark.parametrize("value", ["29", "3601"])
def test_dispatch_horizons_outside_bounds_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_DISPATCH_HORIZON_SECONDS", value)

    with pytest.raises(ConfigurationError, match="between 30 and 3600"):
        WatchSettings.from_environment()


@pytest.mark.parametrize("value", ["5.0", "-30", "lots"])
def test_non_integer_dispatch_horizon_text_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_DISPATCH_HORIZON_SECONDS", value)

    with pytest.raises(ConfigurationError, match="must be an integer"):
        WatchSettings.from_environment()


# --- provider timeout and outage backoff (milestone 3) ----------------------


def test_provider_timeout_and_backoff_defaults() -> None:
    settings = WatchSettings()
    assert settings.provider_call_timeout_seconds == 45
    assert settings.provider_backoff_max_seconds == 3_600


@pytest.mark.parametrize("value", ["1", "45"])
def test_provider_timeouts_within_bounds_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("WATCH_PROVIDER_CALL_TIMEOUT_SECONDS", value)

    settings = WatchSettings.from_environment()
    assert settings.provider_call_timeout_seconds == int(value)


def test_a_provider_timeout_above_the_soft_limit_headroom_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_PROVIDER_CALL_TIMEOUT_SECONDS", "46")

    with pytest.raises(ConfigurationError, match="between 1 and 45"):
        WatchSettings.from_environment()


def test_a_backoff_ceiling_below_the_normal_interval_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_POLL_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("WATCH_PROVIDER_BACKOFF_MAX_SECONDS", "300")

    with pytest.raises(ConfigurationError, match="at least"):
        WatchSettings.from_environment()


def test_a_backoff_ceiling_above_a_day_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCH_PROVIDER_BACKOFF_MAX_SECONDS", "86401")

    with pytest.raises(ConfigurationError, match="between 1 and 86400"):
        WatchSettings.from_environment()
