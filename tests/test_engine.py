import asyncio
from datetime import datetime

from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.schemas import (
    IntentAction,
    OrchestratorRoute,
    ReservationExtraction,
    VenueType,
)


class RecordingProvider:
    def __init__(self, result: ReservationExtraction) -> None:
        self.result = result
        self.prompt: str | None = None
        self.reference_time: datetime | None = None
        self.closed = False

    async def extract(
        self,
        prompt: str,
        reference_time: datetime,
    ) -> ReservationExtraction:
        self.prompt = prompt
        self.reference_time = reference_time
        return self.result

    async def close(self) -> None:
        self.closed = True


def test_engine_passes_raw_prompt_and_routes_validated_extraction() -> None:
    provider = RecordingProvider(
        ReservationExtraction(
            action=IntentAction.SEARCH_AVAILABILITY,
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            party_size=4,
            date="2026-08-22",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        )
    )
    engine = OrchestratorEngine(
        provider,
        clock=lambda: datetime(2026, 8, 18, 12, 30),
    )

    result = asyncio.run(engine.parse("Find Cote for four next Saturday at 7"))

    assert result.route is OrchestratorRoute.BOOKING_SERVICE
    assert provider.prompt == "Find Cote for four next Saturday at 7"
    assert provider.reference_time is not None
    assert provider.reference_time.isoformat() == "2026-08-18T12:30:00-04:00"

    asyncio.run(engine.close())
    assert provider.closed is True


def test_engine_builds_one_orchestrator_per_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The lazily constructed engine is shared, not rebuilt per request."""

    import asyncio as _asyncio

    from fastapi.testclient import TestClient

    from backend.main import create_app, get_orchestrator

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6")
    app = create_app()

    with TestClient(app) as client:
        assert client.get("/health").json()["config"] == "ok"

        async def build_twice() -> tuple[object, object]:
            request = SimpleRequest(app)
            return (
                await get_orchestrator(request),
                await get_orchestrator(request),
            )

        first, second = _asyncio.run(build_twice())

    assert first is second
    assert app.state.orchestrator is first


class SimpleRequest:
    """Minimal stand-in exposing only the app the dependency reads."""

    def __init__(self, app: object) -> None:
        self.app = app
