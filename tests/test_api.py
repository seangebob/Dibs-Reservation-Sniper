from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import app, get_booking_service, get_orchestrator
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
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
    assert response.json() == {"status": "ok", "service": "dibs-mvp"}
