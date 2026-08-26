"""Exploration properties for application logging and startup error visibility."""

import asyncio
from contextlib import redirect_stderr
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
import logging

from fastapi.testclient import TestClient
import pytest

import backend.main as main_module
from backend.config import ConfigurationError, WatchSettings
from backend.db import database
from backend.main import create_app
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.notification_service import (
    LoggingNotificationService,
    WatchEvent,
)


WATCH_ENVIRONMENT_NAMES = (
    "RESERVATION_TIMEZONE",
    "REDIS_URL",
    "WATCH_POLL_INTERVAL_SECONDS",
    "WATCH_POLL_JITTER_SECONDS",
    "WATCH_MAX_POLL_ATTEMPTS",
)
EXERCISED_LOGGER_NAMES = (
    "backend",
    "backend.main",
    "backend.exploration",
    "backend.exploration.worker",
    "backend.services",
    "backend.services.notification_service",
    "backend.workers",
    "backend.workers.scheduler",
)


@dataclass(frozen=True)
class _LoggerState:
    logger: logging.Logger
    handlers: tuple[logging.Handler, ...]
    level: int
    disabled: bool
    propagate: bool
    filters: tuple[logging.Filter, ...]


@dataclass(frozen=True)
class _HandlerState:
    handler: logging.Handler
    level: int
    filters: tuple[logging.Filter, ...]
    formatter: logging.Formatter | None
    stream: object
    has_stored_stream: bool


class _RecordProbe(logging.Filter):
    """Observe producer execution without creating a terminal output path."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def filter(self, record: logging.LogRecord) -> bool:
        self.records.append(record)
        return True


@pytest.fixture
def pristine_backend_logging() -> None:
    """Restore all host-owned logging objects after one isolated observation."""

    root = logging.getLogger()
    for name in EXERCISED_LOGGER_NAMES:
        logging.getLogger(name)

    backend_loggers = [
        value
        for name, value in root.manager.loggerDict.items()
        if name.startswith("backend") and isinstance(value, logging.Logger)
    ]
    loggers = [root, *backend_loggers]
    logger_states = [
        _LoggerState(
            logger=logger,
            handlers=tuple(logger.handlers),
            level=logger.level,
            disabled=logger.disabled,
            propagate=logger.propagate,
            filters=tuple(logger.filters),
        )
        for logger in loggers
    ]

    handlers = {
        id(handler): handler
        for state in logger_states
        for handler in state.handlers
    }.values()
    handler_states = [
        _HandlerState(
            handler=handler,
            level=handler.level,
            filters=tuple(handler.filters),
            formatter=handler.formatter,
            stream=getattr(handler, "stream", None),
            has_stored_stream="stream" in vars(handler),
        )
        for handler in handlers
    ]
    manager_disable = root.manager.disable

    try:
        logging.disable(logging.NOTSET)
        for logger in loggers:
            logger.handlers[:] = []
            logger.setLevel(logging.NOTSET)
            logger.disabled = False
            logger.propagate = True
            logger.filters[:] = []
        yield
    finally:
        for state in handler_states:
            state.handler.level = state.level
            state.handler.filters[:] = state.filters
            state.handler.formatter = state.formatter
            if state.has_stored_stream:
                state.handler.stream = state.stream  # type: ignore[attr-defined]
        for state in logger_states:
            state.logger.handlers[:] = state.handlers
            state.logger.setLevel(state.level)
            state.logger.disabled = state.disabled
            state.logger.propagate = state.propagate
            state.logger.filters[:] = state.filters
        logging.disable(manager_disable)


def _prepare_environment(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str] | None = None,
) -> None:
    for name in WATCH_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-logging-exploration")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    for name, value in (overrides or {}).items():
        monkeypatch.setenv(name, value)


async def _skip_redis(_app: object) -> None:
    """Keep non-Redis properties focused on logging initialization."""


def _representative_watch(event: WatchEvent) -> Watch:
    created_at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    return Watch(
        watch_id=f"watch-logging-{event.value.lower()}",
        status=WatchStatus.ACTIVE,
        query=AvailabilityQuery(
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo-Cambridge, ON",
            party_size=4,
            date="2026-09-05",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        ),
        created_at=created_at,
        updated_at=created_at,
        expires_at=created_at + timedelta(days=10),
        attempts=2,
        max_attempts=10,
    )


def _actionable_watch_settings_errors(
    output: str,
    expected_error: str,
) -> list[str]:
    return [
        line
        for line in output.splitlines()
        if line.startswith("ERROR:backend.main:")
        and "watch" in line.lower()
        and "503" in line
        and expected_error in line
    ]


@pytest.mark.usefixtures("pristine_backend_logging")
@pytest.mark.parametrize(
    ("logger_name", "level", "message"),
    [
        pytest.param(
            "backend.exploration",
            logging.INFO,
            "exploration-backend-info-01",
            id="exploration-info",
        ),
        pytest.param(
            "backend.exploration",
            logging.WARNING,
            "exploration-backend-warning-02",
            id="exploration-warning",
        ),
        pytest.param(
            "backend.exploration.worker",
            logging.INFO,
            "exploration-worker-info-03",
            id="nested-worker-info",
        ),
        pytest.param(
            "backend.exploration.worker",
            logging.WARNING,
            "exploration-worker-warning-04",
            id="nested-worker-warning",
        ),
        pytest.param(
            "backend.workers.scheduler",
            logging.INFO,
            "exploration-scheduler-info-05",
            id="real-descendant-info",
        ),
        pytest.param(
            "backend.workers.scheduler",
            logging.WARNING,
            "exploration-scheduler-warning-06",
            id="real-descendant-warning",
        ),
    ],
)
def test_backend_record_is_terminal_visible_once_after_lifespan_starts(
    monkeypatch: pytest.MonkeyPatch,
    logger_name: str,
    level: int,
    message: str,
) -> None:
    """**Validates: Requirements 1.1, 2.1**"""

    _prepare_environment(monkeypatch)
    monkeypatch.setattr(main_module, "_attach_redis", _skip_redis)
    logger = logging.getLogger(logger_name)
    probe = _RecordProbe()
    logger.addFilter(probe)
    output = StringIO()

    with redirect_stderr(output), TestClient(create_app()):
        logger.log(level, message)

    assert [record.getMessage() for record in probe.records] == [message]
    assert output.getvalue().count(message) == 1


@pytest.mark.usefixtures("pristine_backend_logging")
@pytest.mark.parametrize(
    "event",
    [
        pytest.param(WatchEvent.AVAILABILITY_FOUND, id="availability-found"),
        pytest.param(WatchEvent.BOOKED, id="booked"),
        pytest.param(WatchEvent.EXPIRED, id="expired"),
    ],
)
def test_notification_event_is_terminal_visible_once_after_lifespan_starts(
    monkeypatch: pytest.MonkeyPatch,
    event: WatchEvent,
) -> None:
    """**Validates: Requirements 1.1, 2.1**"""

    _prepare_environment(monkeypatch)
    monkeypatch.setattr(main_module, "_attach_redis", _skip_redis)
    logger = logging.getLogger("backend.services.notification_service")
    probe = _RecordProbe()
    logger.addFilter(probe)
    watch = _representative_watch(event)
    expected = (
        f"watch={watch.watch_id} event={event.value} venue=Cote "
        "date=2026-09-05 party=4 attempts=2"
    )
    output = StringIO()

    with redirect_stderr(output), TestClient(create_app()):
        asyncio.run(LoggingNotificationService().notify(watch, event))

    assert [record.getMessage() for record in probe.records] == [expected]
    assert output.getvalue().count(expected) == 1


class _FailingAsyncRedisClient:
    def __init__(self) -> None:
        self.ping_calls = 0
        self.closed = False

    async def ping(self) -> bool:
        self.ping_calls += 1
        raise OSError("exploration Redis is unavailable")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.usefixtures("pristine_backend_logging")
def test_redis_fallback_warning_is_terminal_visible_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Validates: Requirements 1.1, 2.1**"""

    redis_url = "redis://logging.invalid:6379/0"
    _prepare_environment(monkeypatch, {"REDIS_URL": redis_url})
    client = _FailingAsyncRedisClient()
    monkeypatch.setattr(database, "create_redis_client", lambda _url: client)
    logger = logging.getLogger("backend.main")
    probe = _RecordProbe()
    logger.addFilter(probe)
    expected = f"Redis at {redis_url} is unreachable; watches stay in process memory"
    output = StringIO()

    with redirect_stderr(output), TestClient(create_app()):
        pass

    assert client.ping_calls == 1
    assert client.closed is True
    assert [record.getMessage() for record in probe.records if record.levelno == logging.WARNING] == [expected]
    assert output.getvalue().count(expected) == 1


INVALID_WATCH_ENVIRONMENTS = [
    pytest.param(
        {"RESERVATION_TIMEZONE": "Mars/Olympus_Mons"},
        ("RESERVATION_TIMEZONE",),
        "Unknown RESERVATION_TIMEZONE: Mars/Olympus_Mons",
        id="unknown-reservation-timezone",
    ),
    pytest.param(
        {"REDIS_URL": "http://localhost:6379/0"},
        ("REDIS_URL",),
        "Expected a redis://, rediss://, or unix:// URL.",
        id="unsupported-redis-url-scheme",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "fast"},
        ("WATCH_POLL_INTERVAL_SECONDS",),
        "must be an integer",
        id="interval-non-integer",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "0"},
        ("WATCH_POLL_INTERVAL_SECONDS",),
        "must be a positive integer",
        id="interval-non-positive",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "14"},
        ("WATCH_POLL_INTERVAL_SECONDS",),
        "must be between 15 and 3600",
        id="interval-below-minimum",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "3601"},
        ("WATCH_POLL_INTERVAL_SECONDS",),
        "must be between 15 and 3600",
        id="interval-above-maximum",
    ),
    pytest.param(
        {"WATCH_POLL_JITTER_SECONDS": "noisy"},
        ("WATCH_POLL_JITTER_SECONDS",),
        "must be an integer",
        id="jitter-non-integer",
    ),
    pytest.param(
        {"WATCH_POLL_JITTER_SECONDS": "-1"},
        ("WATCH_POLL_JITTER_SECONDS",),
        "must be a positive integer",
        id="jitter-negative",
    ),
    pytest.param(
        {
            "WATCH_POLL_INTERVAL_SECONDS": "30",
            "WATCH_POLL_JITTER_SECONDS": "30",
        },
        ("WATCH_POLL_JITTER_SECONDS", "WATCH_POLL_INTERVAL_SECONDS"),
        "must be smaller than",
        id="jitter-greater-than-or-equal-to-interval",
    ),
    pytest.param(
        {"WATCH_MAX_POLL_ATTEMPTS": "many"},
        ("WATCH_MAX_POLL_ATTEMPTS",),
        "must be an integer",
        id="max-attempts-non-integer",
    ),
    pytest.param(
        {"WATCH_MAX_POLL_ATTEMPTS": "0"},
        ("WATCH_MAX_POLL_ATTEMPTS",),
        "must be a positive integer",
        id="max-attempts-non-positive",
    ),
]


@pytest.mark.usefixtures("pristine_backend_logging")
@pytest.mark.parametrize(
    ("environment", "affected_fragments", "reason_fragment"),
    INVALID_WATCH_ENVIRONMENTS,
)
def test_invalid_watch_settings_emit_one_actionable_startup_error(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    affected_fragments: tuple[str, ...],
    reason_fragment: str,
) -> None:
    """**Validates: Requirements 1.2, 2.2**"""

    _prepare_environment(monkeypatch, environment)
    with pytest.raises(ConfigurationError) as parsed:
        WatchSettings.from_environment()
    expected_error = str(parsed.value)
    assert reason_fragment in expected_error
    assert all(fragment in expected_error for fragment in affected_fragments)

    fresh = create_app()
    output = StringIO()
    with redirect_stderr(output), TestClient(fresh):
        retained_error = fresh.state.watch_settings_error

    assert isinstance(retained_error, ConfigurationError)
    assert str(retained_error) == expected_error

    actionable_errors = _actionable_watch_settings_errors(
        output.getvalue(),
        expected_error,
    )
    assert len(actionable_errors) == 1
    error_line = actionable_errors[0]
    assert error_line.split(":", 1)[0] == "ERROR"
    assert all(fragment in error_line for fragment in affected_fragments)
    assert reason_fragment in error_line
