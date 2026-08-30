"""`PostgresSettings.from_environment`: validation and disabled-by-default."""

import pytest

from backend.config import (
    ConfigurationError,
    DEFAULT_POSTGRES_POOL_MAX_SIZE,
    DEFAULT_POSTGRES_POOL_MIN_SIZE,
    DEFAULT_POSTGRES_STATEMENT_TIMEOUT_SECONDS,
    PostgresSettings,
)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "POSTGRES_URL",
        "POSTGRES_POOL_MIN_SIZE",
        "POSTGRES_POOL_MAX_SIZE",
        "POSTGRES_STATEMENT_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_omitting_postgres_url_leaves_the_projection_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)

    settings = PostgresSettings.from_environment()

    assert settings.dsn is None
    assert settings.enabled is False


def test_a_valid_postgres_url_is_accepted_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/dibs")

    settings = PostgresSettings.from_environment()

    assert settings.enabled is True
    assert settings.dsn == "postgresql://user:pw@localhost:5432/dibs"
    assert settings.pool_min_size == DEFAULT_POSTGRES_POOL_MIN_SIZE
    assert settings.pool_max_size == DEFAULT_POSTGRES_POOL_MAX_SIZE
    assert (
        settings.statement_timeout_seconds
        == DEFAULT_POSTGRES_STATEMENT_TIMEOUT_SECONDS
    )


def test_the_short_postgres_scheme_is_also_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgres://dibs@db/dibs")

    assert PostgresSettings.from_environment().dsn == "postgres://dibs@db/dibs"


def test_an_empty_postgres_url_fails_loudly_rather_than_silently_disabling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "   ")

    with pytest.raises(ConfigurationError, match="POSTGRES_URL was set but is empty"):
        PostgresSettings.from_environment()


def test_a_non_postgres_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "mysql://root:pw@localhost/dibs")

    with pytest.raises(ConfigurationError, match="Invalid POSTGRES_URL"):
        PostgresSettings.from_environment()


def test_pool_sizes_below_the_bound_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "0")

    with pytest.raises(ConfigurationError, match="POSTGRES_POOL_MAX_SIZE"):
        PostgresSettings.from_environment()


def test_pool_max_below_min_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    monkeypatch.setenv("POSTGRES_POOL_MIN_SIZE", "10")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "3")

    with pytest.raises(
        ConfigurationError, match="POSTGRES_POOL_MAX_SIZE must be at least"
    ):
        PostgresSettings.from_environment()


def test_a_pool_size_above_the_ceiling_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "999")

    with pytest.raises(ConfigurationError, match="POSTGRES_POOL_MAX_SIZE"):
        PostgresSettings.from_environment()


def test_a_non_integer_pool_size_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "big")

    with pytest.raises(ConfigurationError, match="POSTGRES_POOL_MAX_SIZE"):
        PostgresSettings.from_environment()


def test_a_statement_timeout_outside_the_bounds_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/dibs")
    monkeypatch.setenv("POSTGRES_STATEMENT_TIMEOUT_SECONDS", "9999")

    with pytest.raises(ConfigurationError, match="STATEMENT_TIMEOUT"):
        PostgresSettings.from_environment()
