import pytest

from backend.config import ConfigurationError, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "RESERVATION_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_apply_when_only_the_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-test  ")

    settings = Settings.from_environment()

    assert settings.openai_api_key == "sk-test"
    assert settings.openai_model == "gpt-5.6"
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
