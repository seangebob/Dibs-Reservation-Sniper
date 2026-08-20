"""Environment-backed backend configuration."""

from dataclasses import dataclass
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    """Raised when required backend configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    openai_api_key: str
    openai_model: str = "gpt-5.6"
    timezone_name: str = "America/Toronto"

    @classmethod
    def from_environment(cls) -> "Settings":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is not configured")

        model = os.getenv("OPENAI_MODEL", "gpt-5.6").strip()
        timezone_name = os.getenv("RESERVATION_TIMEZONE", "America/Toronto").strip()
        if not model:
            raise ConfigurationError("OPENAI_MODEL cannot be empty")

        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(
                f"Unknown RESERVATION_TIMEZONE: {timezone_name}"
            ) from exc

        return cls(
            openai_api_key=api_key,
            openai_model=model,
            timezone_name=timezone_name,
        )
