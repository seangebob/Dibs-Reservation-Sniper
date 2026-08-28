"""Environment-backed backend configuration."""

from dataclasses import dataclass
import os
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    """Raised when required backend configuration is missing or invalid."""


#: Model identifiers are opaque to us, but a name containing whitespace or
#: shell punctuation is a configuration mistake worth catching at startup
#: rather than as a provider error on the first user request.
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{1,63}$")

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

#: Watches poll on a coarse interval with jitter rather than a tight loop. Both
#: numbers are deliberately conservative: a real booking provider rate-limits
#: or fingerprints anything faster, and nothing here needs second-level latency.
DEFAULT_POLL_INTERVAL_SECONDS = 180
DEFAULT_POLL_JITTER_SECONDS = 30
#: The availability-attempt safety ceiling. This is an upper bound on checks,
#: not a fixed per-watch count: watch creation derives the actual budget from
#: each watch's remaining lifetime and caps it here. The default comfortably
#: covers the worst supported default horizon (a +30-day watch at the earliest
#: 150-second delay needs under 18,000 checks including a possible DST hour).
DEFAULT_MAX_POLL_ATTEMPTS = 25_000
#: Celery is handed a poll only once its due time is within this horizon, so a
#: +7/+30-day watch never becomes a multi-day broker ETA and the durable
#: schedule marker stays the authority for far-future work.
DEFAULT_DISPATCH_HORIZON_SECONDS = 300

_MIN_POLL_INTERVAL_SECONDS = 15
_MAX_POLL_INTERVAL_SECONDS = 3_600
_MIN_MAX_POLL_ATTEMPTS = 1
_MAX_MAX_POLL_ATTEMPTS = 1_000_000
_MIN_DISPATCH_HORIZON_SECONDS = 30
_MAX_DISPATCH_HORIZON_SECONDS = 3_600

#: Longest integer text accepted for a bounded count, rejected before any
#: `int()` conversion so a pathologically long digit string can never be
#: turned into a huge integer. Comfortably fits every bound we accept.
_MAX_COUNT_TEXT_LENGTH = 12


def _bounded_int(name: str, default: int, *, allow_zero: bool = False) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 0 or (value == 0 and not allow_zero):
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _bounded_count(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Parse a positive integer setting constrained to a closed range.

    Unlike `_bounded_int`, this rejects signs, decimals, and overlong text
    before conversion, so a hostile or fat-fingered value can neither be parsed
    into an enormous integer nor slip past as a signed number.
    """

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if len(raw) > _MAX_COUNT_TEXT_LENGTH or not raw.isdigit():
        # `str.isdigit()` is true only for a run of ASCII digits, so it rejects
        # "+5", "-5", "5.5", "1_000", and whitespace while accepting "999".
        raise ConfigurationError(f"{name} must be an integer")
    value = int(raw)
    if value < minimum:
        if minimum <= 1:
            # Preserve the historical wording for the common zero case.
            raise ConfigurationError(f"{name} must be a positive integer")
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    if value > maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


@dataclass(frozen=True, slots=True)
class WatchSettings:
    """Configuration needed by watch storage and workers only.

    Keeping this separate from language-model settings lets direct watch APIs
    and Celery workers use Redis without requiring an unrelated OpenAI key.
    """

    timezone_name: str = DEFAULT_TIMEZONE
    redis_url: str = DEFAULT_REDIS_URL
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    poll_jitter_seconds: int = DEFAULT_POLL_JITTER_SECONDS
    max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS
    dispatch_horizon_seconds: int = DEFAULT_DISPATCH_HORIZON_SECONDS

    @classmethod
    def from_environment(cls) -> "WatchSettings":
        timezone_name = os.getenv("RESERVATION_TIMEZONE", DEFAULT_TIMEZONE).strip()
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigurationError(
                f"Unknown RESERVATION_TIMEZONE: {timezone_name}"
            ) from exc

        redis_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL).strip()
        if urlparse(redis_url).scheme not in {"redis", "rediss", "unix"}:
            raise ConfigurationError(
                f"Invalid REDIS_URL: {redis_url!r}. Expected a redis://, "
                "rediss://, or unix:// URL."
            )

        interval = _bounded_int(
            "WATCH_POLL_INTERVAL_SECONDS",
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        if not _MIN_POLL_INTERVAL_SECONDS <= interval <= _MAX_POLL_INTERVAL_SECONDS:
            raise ConfigurationError(
                "WATCH_POLL_INTERVAL_SECONDS must be between "
                f"{_MIN_POLL_INTERVAL_SECONDS} and {_MAX_POLL_INTERVAL_SECONDS}"
            )

        jitter = _bounded_int(
            "WATCH_POLL_JITTER_SECONDS",
            DEFAULT_POLL_JITTER_SECONDS,
            allow_zero=True,
        )
        if jitter >= interval:
            raise ConfigurationError(
                "WATCH_POLL_JITTER_SECONDS must be smaller than "
                "WATCH_POLL_INTERVAL_SECONDS"
            )

        attempts = _bounded_count(
            "WATCH_MAX_POLL_ATTEMPTS",
            DEFAULT_MAX_POLL_ATTEMPTS,
            minimum=_MIN_MAX_POLL_ATTEMPTS,
            maximum=_MAX_MAX_POLL_ATTEMPTS,
        )

        dispatch_horizon = _bounded_count(
            "WATCH_DISPATCH_HORIZON_SECONDS",
            DEFAULT_DISPATCH_HORIZON_SECONDS,
            minimum=_MIN_DISPATCH_HORIZON_SECONDS,
            maximum=_MAX_DISPATCH_HORIZON_SECONDS,
        )

        return cls(
            timezone_name=timezone_name,
            redis_url=redis_url,
            poll_interval_seconds=interval,
            poll_jitter_seconds=jitter,
            max_poll_attempts=attempts,
            dispatch_horizon_seconds=dispatch_horizon,
        )


@dataclass(frozen=True, slots=True)
class Settings:
    openai_api_key: str
    openai_model: str = DEFAULT_MODEL
    timezone_name: str = DEFAULT_TIMEZONE
    redis_url: str = DEFAULT_REDIS_URL
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    poll_jitter_seconds: int = DEFAULT_POLL_JITTER_SECONDS
    max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS
    dispatch_horizon_seconds: int = DEFAULT_DISPATCH_HORIZON_SECONDS

    @classmethod
    def from_environment(cls) -> "Settings":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not configured. Set it in the environment "
                "before starting the service."
            )

        model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip()
        if not model:
            raise ConfigurationError("OPENAI_MODEL cannot be empty")
        if not _MODEL_NAME_PATTERN.fullmatch(model):
            raise ConfigurationError(
                f"Invalid OPENAI_MODEL name: {model!r}. Expected an identifier "
                "such as 'gpt-4o-mini'."
            )

        watch = WatchSettings.from_environment()
        return cls(
            openai_api_key=api_key,
            openai_model=model,
            timezone_name=watch.timezone_name,
            redis_url=watch.redis_url,
            poll_interval_seconds=watch.poll_interval_seconds,
            poll_jitter_seconds=watch.poll_jitter_seconds,
            max_poll_attempts=watch.max_poll_attempts,
            dispatch_horizon_seconds=watch.dispatch_horizon_seconds,
        )
