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
DEFAULT_MAX_POLL_ATTEMPTS = 200

_MIN_POLL_INTERVAL_SECONDS = 15
_MAX_POLL_INTERVAL_SECONDS = 3_600


@dataclass(frozen=True, slots=True)
class Settings:
    openai_api_key: str
    openai_model: str = DEFAULT_MODEL
    timezone_name: str = DEFAULT_TIMEZONE
    redis_url: str = DEFAULT_REDIS_URL
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    poll_jitter_seconds: int = DEFAULT_POLL_JITTER_SECONDS
    max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS

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

        interval = cls._bounded_int(
            "WATCH_POLL_INTERVAL_SECONDS",
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        if not _MIN_POLL_INTERVAL_SECONDS <= interval <= _MAX_POLL_INTERVAL_SECONDS:
            raise ConfigurationError(
                "WATCH_POLL_INTERVAL_SECONDS must be between "
                f"{_MIN_POLL_INTERVAL_SECONDS} and {_MAX_POLL_INTERVAL_SECONDS}"
            )

        jitter = cls._bounded_int(
            "WATCH_POLL_JITTER_SECONDS",
            DEFAULT_POLL_JITTER_SECONDS,
            allow_zero=True,
        )
        # Jitter is applied symmetrically around the interval, so a jitter as
        # wide as the interval could otherwise schedule a poll in the past.
        if jitter >= interval:
            raise ConfigurationError(
                "WATCH_POLL_JITTER_SECONDS must be smaller than "
                "WATCH_POLL_INTERVAL_SECONDS"
            )

        attempts = cls._bounded_int(
            "WATCH_MAX_POLL_ATTEMPTS",
            DEFAULT_MAX_POLL_ATTEMPTS,
        )

        return cls(
            openai_api_key=api_key,
            openai_model=model,
            timezone_name=timezone_name,
            redis_url=redis_url,
            poll_interval_seconds=interval,
            poll_jitter_seconds=jitter,
            max_poll_attempts=attempts,
        )

    @staticmethod
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
