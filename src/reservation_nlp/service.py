"""Application service for reservation-intent parsing."""

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from reservation_nlp.models import ReservationIntent
from reservation_nlp.providers import IntentProvider


Clock = Callable[[], datetime]


class ReservationParser:
    """Coordinates reference-time handling and provider extraction."""

    def __init__(
        self,
        provider: IntentProvider,
        *,
        timezone_name: str = "America/Toronto",
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or (lambda: datetime.now(self._timezone))

    async def parse(self, prompt: str) -> ReservationIntent:
        reference_time = self._clock()
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=self._timezone)
        else:
            reference_time = reference_time.astimezone(self._timezone)

        intent = await self._provider.extract(prompt, reference_time)
        if intent.date is None or date.fromisoformat(intent.date) >= reference_time.date():
            return intent

        question = "That date has already passed. What future date would you like?"
        if intent.missing_info:
            existing_question = intent.missing_info.rstrip("?")
            existing_question = existing_question[0].lower() + existing_question[1:]
            question = (
                "That date has already passed. What future date would you like, "
                f"and {existing_question}?"
            )

        return ReservationIntent(
            restaurant=intent.restaurant,
            party_size=intent.party_size,
            date=None,
            preferred_time=intent.preferred_time,
            missing_info=question,
        )

    async def close(self) -> None:
        """Release resources held by the configured provider."""

        await self._provider.close()
