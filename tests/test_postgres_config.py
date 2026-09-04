"""`PostgresSettings.from_environment`: validation and disabled-by-default."""

import pytest

from backend.config import (
    ConfigurationError,
    DEFAULT_POSTGRES_POOL_MAX_SIZE,
    DEFAULT_POSTGRES_POOL_MIN_SIZE,
    DEFAULT_POSTGRES_STATEMENT_TIMEOUT_SECONDS,
    PostgresSettings,
    redact_dsn,
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


# --- credential redaction --------------------------------------------------
#
# `main.py` logs the message of every `ConfigurationError` raised here and by
# `create_pool`, so anything interpolated into one is written to the startup
# log. The DSN carries a password.

SECRET = "hunter2"


def test_a_rejected_dsn_names_the_target_without_its_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", f"mysql://root:{SECRET}@db.internal:3306/dibs")

    with pytest.raises(ConfigurationError) as raised:
        PostgresSettings.from_environment()

    message = str(raised.value)
    assert SECRET not in message
    assert "root" not in message
    # Still diagnosable: the operator can see which server and which scheme.
    assert "db.internal:3306/dibs" in message
    assert "mysql" in message


def test_settings_do_not_render_the_dsn_in_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("POSTGRES_URL", f"postgresql://dibs:{SECRET}@localhost/dibs")

    settings = PostgresSettings.from_environment()

    assert SECRET not in repr(settings)
    assert settings.dsn == f"postgresql://dibs:{SECRET}@localhost/dibs"


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        (
            "postgresql://dibs:hunter2@localhost:5432/dibs",
            "postgresql://localhost:5432/dibs",
        ),
        ("postgres://dibs@db/dibs", "postgres://db/dibs"),
        ("postgresql://localhost/dibs", "postgresql://localhost/dibs"),
        # Query values are dropped whole: the safe set differs per driver.
        (
            "postgresql://dibs:hunter2@localhost/dibs?password=hunter2&sslmode=require",
            "postgresql://localhost/dibs",
        ),
        # A non-numeric port makes `urlparse().port` raise; disclose nothing.
        (
            "postgresql://dibs:hunter2@localhost:not-a-port/dibs",
            "<unparseable connection URL>",
        ),
        ("", "unknown-scheme://unknown-host"),
        ("nonsense", "unknown-scheme://unknown-hostnonsense"),
    ],
)
def test_redact_dsn_never_returns_a_credential(dsn: str, expected: str) -> None:
    assert redact_dsn(dsn) == expected
    assert "hunter2" not in redact_dsn(dsn)
