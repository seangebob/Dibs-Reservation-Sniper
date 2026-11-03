"""Shared pytest configuration for reservation NLP tests."""

import pytest


#: Every environment variable the backend reads. Tests start with none of
#: them set, so a developer's shell (a running local Redis, a real OpenAI key,
#: tuned poll intervals) can never change what a test does -- or receive its
#: writes. A test that needs one sets it with monkeypatch.
_BACKEND_ENV = (
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_PARALLELISM",
    "ARGON2_TIME_COST",
    "DASHBOARD_BASE_URL",
    "FRONTEND_ORIGINS",
    "LOGIN_IP_THROTTLE_MAX_ATTEMPTS",
    "LOGIN_THROTTLE_MAX_ATTEMPTS",
    "LOGIN_THROTTLE_WINDOW_SECONDS",
    "MIGRATIONS_DIRECTORY",
    "MOCK_BOOKING_RETENTION_SECONDS",
    "MOCK_SLOT_CAPACITY",
    "MOCK_SLOT_IDLE_TTL_SECONDS",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "POSTGRES_POOL_MAX_SIZE",
    "POSTGRES_POOL_MIN_SIZE",
    "POSTGRES_STATEMENT_TIMEOUT_SECONDS",
    "POSTGRES_URL",
    "PROMPT_THROTTLE_MAX_REQUESTS",
    "PROMPT_THROTTLE_WINDOW_SECONDS",
    "RESERVATION_TIMEZONE",
    "SESSION_TTL_SECONDS",
    "SMTP_FROM",
    "SMTP_HOST",
    "SMTP_PASSWORD",
    "SMTP_PORT",
    "SMTP_STARTTLS",
    "SMTP_TIMEOUT_SECONDS",
    "SMTP_USERNAME",
    "WATCH_DISPATCH_HORIZON_SECONDS",
    "WATCH_MAX_POLL_ATTEMPTS",
    "WATCH_POLL_INTERVAL_SECONDS",
    "WATCH_POLL_JITTER_SECONDS",
    "WATCH_PROVIDER_BACKOFF_MAX_SECONDS",
    "WATCH_PROVIDER_CALL_TIMEOUT_SECONDS",
    "WATCH_RECOVERY_LEADER_LEASE_SECONDS",
    "WATCH_RECOVERY_SWEEP_SECONDS",
    "WATCH_TERMINAL_RETENTION_SECONDS",
)

#: Nothing listens on port 1, so the app takes its in-memory path at once.
#: Unsetting REDIS_URL is not enough: it defaults to localhost:6379, which is
#: exactly where the docker-compose dev Redis runs.
_UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture(autouse=True)
def _isolated_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _BACKEND_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REDIS_URL", _UNREACHABLE_REDIS)
