"""Main AI orchestration engine for Milestone 1."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.orchestrator.providers import IntentProvider
from backend.orchestrator.schemas import ReservationIntent
from backend.orchestrator.validator import IntentValidator


Clock = Callable[[], datetime]


class OrchestratorEngine:
    """Coordinates extraction, deterministic validation, and routing."""

    def __init__(
        self,
        provider: IntentProvider,
        *,
        validator: IntentValidator | None = None,
        timezone_name: str = "America/Toronto",
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._validator = validator or IntentValidator()
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or (lambda: datetime.now(self._timezone))

    async def parse(self, prompt: str) -> ReservationIntent:
        """Parse one prompt without executing its resulting route."""

        reference_time = self._clock()
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=self._timezone)
        else:
            reference_time = reference_time.astimezone(self._timezone)

        extraction = await self._provider.extract(prompt, reference_time)
        return self._validator.validate(extraction, reference_time)

    async def close(self) -> None:
        """Release resources held by the configured provider."""

        await self._provider.close()
