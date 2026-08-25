"""Environment-backed backend configuration."""

from dataclasses import dataclass
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    """Raised when required backend configuration is missing or invalid."""


#: Model identifiers are opaque to us, but a name containing whitespace or
#: shell punctuation is a configuration mistake worth catching at startup
#: rather than as a provider error on the first user request.
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{1,63}$")

DEFAULT_MODEL = "gpt-5.6"
DEFAULT_TIMEZONE = "America/Toronto"


@dataclass(frozen=True, slots=True)
class Settings:
    openai_api_key: str
    openai_model: str = DEFAULT_MODEL
    timezone_name: str = DEFAULT_TIMEZONE

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
                "such as 'gpt-5.6'."
            )

        timezone_name = os.getenv("RESERVATION_TIMEZONE", DEFAULT_TIMEZONE).strip()
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigurationError(
                f"Unknown RESERVATION_TIMEZONE: {timezone_name}"
            ) from exc

        return cls(
            openai_api_key=api_key,
            openai_model=model,
            timezone_name=timezone_name,
        )
