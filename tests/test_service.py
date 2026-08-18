import asyncio
from datetime import datetime

from reservation_nlp.models import ReservationIntent
from reservation_nlp.service import ReservationParser


class RecordingProvider:
    def __init__(self, result: ReservationIntent) -> None:
        self.result = result
        self.prompt: str | None = None
        self.reference_time: datetime | None = None

    async def extract(
        self,
        prompt: str,
        reference_time: datetime,
    ) -> ReservationIntent:
        self.prompt = prompt
        self.reference_time = reference_time
        return self.result


def test_parser_passes_raw_prompt_with_kw_reference_time() -> None:
    result = ReservationIntent(
        restaurant="Cote",
        party_size=4,
        date="2026-08-22",
        preferred_time="19:00",
        missing_info=None,
    )
    provider = RecordingProvider(result)
    parser = ReservationParser(
        provider,
        clock=lambda: datetime(2026, 8, 18, 12, 30),
    )

    actual = asyncio.run(parser.parse("Cote for four next Saturday at 7"))

    assert actual == result
    assert provider.prompt == "Cote for four next Saturday at 7"
    assert provider.reference_time is not None
    assert provider.reference_time.isoformat() == "2026-08-18T12:30:00-04:00"


def test_parser_converts_past_date_to_clarification() -> None:
    result = ReservationIntent(
        restaurant="Cote",
        party_size=4,
        date="2026-08-17",
        preferred_time="19:00",
        missing_info=None,
    )
    provider = RecordingProvider(result)
    parser = ReservationParser(
        provider,
        clock=lambda: datetime(2026, 8, 18, 12, 30),
    )

    actual = asyncio.run(parser.parse("Cote for four yesterday at 7"))

    assert actual.date is None
    assert actual.is_complete is False
    assert actual.missing_info == (
        "That date has already passed. What future date would you like?"
    )
