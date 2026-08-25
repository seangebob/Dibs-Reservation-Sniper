import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from backend.integrations.base import (
    AdapterError,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import app, create_app, get_booking_service, get_orchestrator
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import ProviderError
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationExtraction,
    ReservationIntent,
    VenueType,
)
from backend.services.booking_service import BookingService


class StubEngine:
    def __init__(self, result: ReservationIntent) -> None:
        self.result = result
        self.prompt: str | None = None

    async def parse(self, prompt: str) -> ReservationIntent:
        self.prompt = prompt
        return self.result


def ready_intent(
    *,
    action: IntentAction = IntentAction.BOOK_RESERVATION,
    route: OrchestratorRoute = OrchestratorRoute.BOOKING_SERVICE,
) -> ReservationIntent:
    return ReservationIntent(
        status=IntentStatus.READY,
        route=route,
        action=action,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo, ON",
        party_size=4,
        date="2026-08-22",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[],
        clarification_question=None,
    )


def incomplete_intent() -> ReservationIntent:
    return ReservationIntent(
        status=IntentStatus.NEEDS_CLARIFICATION,
        route=OrchestratorRoute.CLARIFICATION,
        action=IntentAction.BOOK_RESERVATION,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo, ON",
        party_size=None,
        date="2026-08-22",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[MissingField.PARTY_SIZE],
        clarification_question="How many guests or participants are there?",
    )


def test_main_orchestrator_route_returns_clean_routing_json() -> None:
    engine = StubEngine(ready_intent())
    app.dependency_overrides[get_orchestrator] = lambda: engine

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["route"] == "BOOKING_SERVICE"
    assert response.json()["action"] == "BOOK_RESERVATION"
    assert engine.prompt == "Book Cote for four next Saturday at 7 pm"


def test_router_returns_clarification_instead_of_execution_route() -> None:
    engine = StubEngine(incomplete_intent())
    app.dependency_overrides[get_orchestrator] = lambda: engine

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["route"] == "CLARIFICATION"
    assert response.json()["missing_fields"] == ["party_size"]


def test_parse_and_book_returns_mock_confirmation() -> None:
    engine = StubEngine(ready_intent())
    service = BookingService(
        MockBookingAdapter(
            clock=lambda: datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
        )
    )
    app.dependency_overrides[get_orchestrator] = lambda: engine
    app.dependency_overrides[get_booking_service] = lambda: service

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "MOCK_BOOKED"
    assert body["booking"]["provider"] == "mock"
    assert body["booking"]["status"] == "MOCK_CONFIRMED"
    assert "No real venue" in body["message"]


def test_parse_and_book_can_return_search_results_without_booking() -> None:
    engine = StubEngine(ready_intent(action=IntentAction.SEARCH_AVAILABILITY))
    service = BookingService(MockBookingAdapter())
    app.dependency_overrides[get_orchestrator] = lambda: engine
    app.dependency_overrides[get_booking_service] = lambda: service

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Find Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "AVAILABILITY_FOUND"
    assert response.json()["booking"] is None
    assert len(response.json()["slots"]) == 1


def test_parse_and_book_preserves_clarification_safety_boundary() -> None:
    engine = StubEngine(incomplete_intent())
    app.dependency_overrides[get_orchestrator] = lambda: engine
    app.dependency_overrides[get_booking_service] = lambda: BookingService(
        MockBookingAdapter()
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "CLARIFICATION_REQUIRED"
    assert response.json()["slots"] == []
    assert response.json()["booking"] is None


def test_parse_and_book_defers_watch_to_milestone_three() -> None:
    engine = StubEngine(
        ready_intent(
            action=IntentAction.CREATE_WATCH,
            route=OrchestratorRoute.WATCH_SERVICE,
        )
    )
    app.dependency_overrides[get_orchestrator] = lambda: engine
    app.dependency_overrides[get_booking_service] = lambda: BookingService(
        MockBookingAdapter()
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Watch Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "WATCH_REQUIRED"
    assert "Milestone 3" in response.json()["message"]


def test_router_rejects_empty_prompt_before_engine_call() -> None:
    engine = StubEngine(ready_intent())
    app.dependency_overrides[get_orchestrator] = lambda: engine

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": "  "},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert engine.prompt is None


def test_health_route_identifies_mvp() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "dibs-mvp",
        "config": "ok",
    }


class FailingAdapter(MockBookingAdapter):
    """Adapter that surfaces raw platform errors from a search."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def search_availability(self, query):  # type: ignore[no-untyped-def]
        raise self._error


def override(engine: object, service: object | None = None) -> None:
    app.dependency_overrides[get_orchestrator] = lambda: engine
    if service is not None:
        app.dependency_overrides[get_booking_service] = lambda: service


def test_parse_response_matches_the_reservation_intent_shape() -> None:
    override(StubEngine(ready_intent()))

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert set(body) == set(ReservationIntent.model_fields)
    assert body["market"] == "Kitchener-Waterloo, ON"
    assert body["status"] == "READY"


def test_parse_and_book_response_matches_the_execution_result_shape() -> None:
    override(StubEngine(ready_intent()), BookingService(MockBookingAdapter()))

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert set(body) == {"status", "intent", "slots", "booking", "message"}
    assert set(body["intent"]) == set(ReservationIntent.model_fields)
    assert set(body["booking"]) == {
        "booking_id",
        "provider",
        "status",
        "slot",
        "created_at",
    }


def test_unknown_slot_from_an_adapter_maps_to_404() -> None:
    override(
        StubEngine(ready_intent()),
        BookingService(FailingAdapter(SlotNotFoundError("no such slot"))),
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "no such slot"}


def test_taken_slot_from_an_adapter_maps_to_409() -> None:
    override(
        StubEngine(ready_intent()),
        BookingService(FailingAdapter(SlotUnavailableError("already booked"))),
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "already booked"}


def test_generic_adapter_failure_maps_to_502() -> None:
    override(
        StubEngine(ready_intent()),
        BookingService(FailingAdapter(AdapterError("platform down"))),
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "platform down"}


def test_language_model_failure_maps_to_502() -> None:
    class BrokenEngine:
        async def parse(self, prompt: str) -> ReservationIntent:
            raise ProviderError("The language model provider request failed")

    override(BrokenEngine())

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "provider request failed" in response.json()["detail"]


def test_missing_api_key_is_reported_as_503_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fresh = create_app()

    with TestClient(fresh) as client:
        response = client.post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )

        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]
        assert client.get("/health").json()["config"] == "error"


def test_invalid_model_name_is_detected_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "not a model")
    fresh = create_app()

    with TestClient(fresh) as client:
        assert client.get("/health").json()["config"] == "error"

        response = client.post(
            "/api/orchestrator/parse",
            json={"prompt": "Book Cote for four on Saturday at 7 pm"},
        )

        assert response.status_code == 503
        assert "OPENAI_MODEL" in response.json()["detail"]


def test_valid_configuration_reports_a_healthy_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6")
    fresh = create_app()

    with TestClient(fresh) as client:
        assert client.get("/health").json()["config"] == "ok"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": None},
        {"prompt": 7},
        {"prompt": ""},
        {"prompt": "\n\t  "},
        {"prompt": "book a table", "extra": "field"},
        {"prompt": "x" * 2001},
    ],
)
def test_malformed_request_bodies_return_422(payload: dict[str, object]) -> None:
    engine = StubEngine(ready_intent())
    override(engine)

    try:
        response = TestClient(app).post("/api/orchestrator/parse", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert engine.prompt is None


def test_unparseable_json_body_returns_422() -> None:
    override(StubEngine(ready_intent()))

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_longest_accepted_prompt_still_reaches_the_engine() -> None:
    engine = StubEngine(ready_intent())
    override(engine)
    prompt = "b" * 2000

    try:
        response = TestClient(app).post(
            "/api/orchestrator/parse",
            json={"prompt": prompt},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert engine.prompt == prompt


def test_simultaneous_parses_do_not_cross_prompts() -> None:
    """Each concurrent request must get the intent parsed from its own text."""

    class EchoProvider:
        async def extract(self, prompt: str, reference_time: datetime):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            return ReservationExtraction(
                action=IntentAction.SEARCH_AVAILABILITY,
                venue_name=prompt,
                venue_type=VenueType.RESTAURANT,
                party_size=len(prompt),
                date="2026-08-22",
                preferred_time="19:00",
                time_window=None,
                duration_minutes=None,
                special_requests=[],
            )

        async def close(self) -> None:
            return None

    engine = OrchestratorEngine(
        EchoProvider(),
        clock=lambda: datetime(2026, 8, 18, 12, 30, tzinfo=ZoneInfo("America/Toronto")),
    )
    prompts = ["a", "bb", "ccc", "dddd", "eeeee"]

    async def run_all() -> list[ReservationIntent]:
        return await asyncio.gather(*(engine.parse(prompt) for prompt in prompts))

    results = asyncio.run(run_all())

    assert [result.venue_name for result in results] == prompts
    assert [result.party_size for result in results] == [len(p) for p in prompts]


def test_default_booking_service_is_wired_without_overrides() -> None:
    """The app must reach the mock adapter with no test doubles injected."""

    app.dependency_overrides[get_orchestrator] = lambda: StubEngine(
        ready_intent(action=IntentAction.SEARCH_AVAILABILITY)
    )

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Find Cote for four on Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "AVAILABILITY_FOUND"
    assert response.json()["slots"][0]["provider"] == "mock"
