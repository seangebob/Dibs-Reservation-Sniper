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
from backend.db.repositories.watches import InMemoryWatchRepository
from backend.main import (
    app,
    create_app,
    get_booking_service,
    get_orchestrator,
    get_watch_service,
)
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
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


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
        market="Kitchener-Waterloo-Cambridge, ON",
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
        market="Kitchener-Waterloo-Cambridge, ON",
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


def test_parse_and_book_opens_a_background_watch() -> None:
    engine = StubEngine(
        ready_intent(
            action=IntentAction.CREATE_WATCH,
            route=OrchestratorRoute.WATCH_SERVICE,
        )
    )
    queue = RecordingTaskQueue()
    repository = InMemoryWatchRepository()
    watch_service = WatchService(repository, MockBookingAdapter(), queue)
    app.dependency_overrides[get_orchestrator] = lambda: engine
    app.dependency_overrides[get_booking_service] = lambda: BookingService(
        MockBookingAdapter()
    )
    app.dependency_overrides[get_watch_service] = lambda: watch_service

    try:
        response = TestClient(app).post(
            "/api/parse-and-book",
            json={"prompt": "Watch Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "WATCH_CREATED"
    assert body["watch_id"] is not None
    assert body["booking"] is None
    # Creating the watch must also have dispatched its first check.
    assert queue.dispatches == [(body["watch_id"], 0.0)]


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
        "watch_store": "memory",
        "watch_queue": "asyncio",
        "queue_readiness": "ready",
        "recovery_readiness": "unknown",
    }


class FailingAdapter(MockBookingAdapter):
    """Adapter that surfaces raw platform errors from a search."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def search_availability(self, query):  # type: ignore[no-untyped-def]
        raise self._error


@pytest.fixture(autouse=True)
def started_shared_app():
    """Give the module-level `app` the watch settings startup would have set.

    These tests drive the shared `app` with a bare `TestClient(app)`, which
    never runs lifespan. Uvicorn always does, so an app serving requests with
    no watch settings and no retained configuration error is a state that
    cannot occur in production; `get_watch_service` now rejects it outright.
    Populating the settings here keeps these tests representing a started
    application rather than that impossible one. The rejection itself is
    covered directly in `tests/test_watch_api.py`.
    """

    previous = app.state.watch_settings
    if previous is None:
        app.state.watch_settings = WatchSettings()
    yield
    app.state.watch_settings = previous


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
    assert body["market"] == "Kitchener-Waterloo-Cambridge, ON"
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
    assert set(body) == {
        "status",
        "intent",
        "slots",
        "booking",
        "watch_id",
        "message",
    }
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


# Preservation baselines for watch startup and infrastructure selection.
import logging
import sys
import types
from typing import Any

from starlette.requests import Request

from backend.config import ConfigurationError, WatchSettings
from backend.db import database
from backend.db.repositories.watches import RedisWatchRepository
from backend.workers.queue import AsyncioTaskQueue, CeleryTaskQueue


_PRESERVATION_ENVIRONMENT_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "RESERVATION_TIMEZONE",
    "REDIS_URL",
    "WATCH_POLL_INTERVAL_SECONDS",
    "WATCH_POLL_JITTER_SECONDS",
    "WATCH_MAX_POLL_ATTEMPTS",
)
_PRESERVATION_WATCH_QUERY = {
    "venue_name": "Cote",
    "venue_type": "RESTAURANT",
    "market": "Kitchener-Waterloo-Cambridge, ON",
    "party_size": 4,
    "date": "2026-09-05",
    "preferred_time": "19:00",
    "time_window": None,
    "duration_minutes": None,
    "special_requests": [],
}
_INVALID_WATCH_SETTINGS_CASES = [
    pytest.param(
        {"RESERVATION_TIMEZONE": "Mars/Olympus_Mons"},
        "Unknown RESERVATION_TIMEZONE: Mars/Olympus_Mons",
        id="unknown-reservation-timezone",
    ),
    pytest.param(
        {"REDIS_URL": "http://localhost:6379/0"},
        "Invalid REDIS_URL: 'http://localhost:6379/0'. Expected a redis://, "
        "rediss://, or unix:// URL.",
        id="unsupported-redis-url-scheme",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "fast"},
        "WATCH_POLL_INTERVAL_SECONDS must be an integer",
        id="interval-non-integer",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "0"},
        "WATCH_POLL_INTERVAL_SECONDS must be a positive integer",
        id="interval-non-positive",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "14"},
        "WATCH_POLL_INTERVAL_SECONDS must be between 15 and 3600",
        id="interval-below-minimum",
    ),
    pytest.param(
        {"WATCH_POLL_INTERVAL_SECONDS": "3601"},
        "WATCH_POLL_INTERVAL_SECONDS must be between 15 and 3600",
        id="interval-above-maximum",
    ),
    pytest.param(
        {"WATCH_POLL_JITTER_SECONDS": "noisy"},
        "WATCH_POLL_JITTER_SECONDS must be an integer",
        id="jitter-non-integer",
    ),
    pytest.param(
        {"WATCH_POLL_JITTER_SECONDS": "-1"},
        "WATCH_POLL_JITTER_SECONDS must be a positive integer",
        id="jitter-negative",
    ),
    pytest.param(
        {
            "WATCH_POLL_INTERVAL_SECONDS": "30",
            "WATCH_POLL_JITTER_SECONDS": "30",
        },
        "WATCH_POLL_JITTER_SECONDS must be smaller than "
        "WATCH_POLL_INTERVAL_SECONDS",
        id="jitter-greater-than-or-equal-to-interval",
    ),
    pytest.param(
        {"WATCH_MAX_POLL_ATTEMPTS": "many"},
        "WATCH_MAX_POLL_ATTEMPTS must be an integer",
        id="max-attempts-non-integer",
    ),
    pytest.param(
        {"WATCH_MAX_POLL_ATTEMPTS": "0"},
        "WATCH_MAX_POLL_ATTEMPTS must be a positive integer",
        id="max-attempts-non-positive",
    ),
]
_VALID_WATCH_SETTINGS_CASES = [
    pytest.param(
        {
            "RESERVATION_TIMEZONE": "UTC",
            "REDIS_URL": "unix:///tmp/dibs-preservation.sock",
            "WATCH_POLL_INTERVAL_SECONDS": "15",
            "WATCH_POLL_JITTER_SECONDS": "0",
            "WATCH_MAX_POLL_ATTEMPTS": "1",
        },
        WatchSettings(
            timezone_name="UTC",
            redis_url="unix:///tmp/dibs-preservation.sock",
            poll_interval_seconds=15,
            poll_jitter_seconds=0,
            max_poll_attempts=1,
        ),
        id="minimum-interval-zero-jitter",
    ),
    pytest.param(
        {
            "RESERVATION_TIMEZONE": "America/Vancouver",
            "REDIS_URL": "rediss://cache.example.test:6380/2",
            "WATCH_POLL_INTERVAL_SECONDS": "30",
            "WATCH_POLL_JITTER_SECONDS": "5",
            "WATCH_MAX_POLL_ATTEMPTS": "7",
        },
        WatchSettings(
            timezone_name="America/Vancouver",
            redis_url="rediss://cache.example.test:6380/2",
            poll_interval_seconds=30,
            poll_jitter_seconds=5,
            max_poll_attempts=7,
        ),
        id="representative-interval-jitter",
    ),
    pytest.param(
        {
            "RESERVATION_TIMEZONE": "America/Toronto",
            "REDIS_URL": "redis://cache.example.test:6379/0",
            "WATCH_POLL_INTERVAL_SECONDS": "3600",
            "WATCH_POLL_JITTER_SECONDS": "3599",
            "WATCH_MAX_POLL_ATTEMPTS": "999",
        },
        WatchSettings(
            timezone_name="America/Toronto",
            redis_url="redis://cache.example.test:6379/0",
            poll_interval_seconds=3600,
            poll_jitter_seconds=3599,
            max_poll_attempts=999,
        ),
        id="maximum-interval-valid-jitter",
    ),
]


class _PreservationRedisClient:
    def __init__(self, *, reachable: bool) -> None:
        self.reachable = reachable
        self.ping_calls = 0
        self.close_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if not self.reachable:
            raise OSError("preservation Redis is unavailable")
        return True

    async def aclose(self) -> None:
        self.close_calls += 1


def _prepare_preservation_environment(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str | None] | None = None,
) -> None:
    for name in _PRESERVATION_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-preservation")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    for name, value in (overrides or {}).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _assert_watch_service_settings(
    fresh_app: Any,
    settings: WatchSettings,
) -> None:
    service = fresh_app.state.watch_service
    assert service._repository is fresh_app.state.watch_repository
    assert service._queue is fresh_app.state.watch_queue
    assert service._schedule.interval_seconds == float(
        settings.poll_interval_seconds
    )
    assert service._schedule.jitter_seconds == float(settings.poll_jitter_seconds)
    assert service._max_attempts == settings.max_poll_attempts
    assert str(service._reservation_timezone) == settings.timezone_name


@pytest.mark.parametrize(
    ("environment", "expected_error"),
    _INVALID_WATCH_SETTINGS_CASES,
)
def test_invalid_watch_settings_preserve_startup_dependency_and_api_behavior(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected_error: str,
) -> None:
    """**Validates: Requirements 3.3**"""

    _prepare_preservation_environment(monkeypatch, environment)
    fresh = create_app()

    with TestClient(fresh) as client:
        retained_error = fresh.state.watch_settings_error
        assert fresh.state.watch_settings is None
        assert isinstance(retained_error, ConfigurationError)
        assert str(retained_error) == expected_error

        with pytest.raises(ConfigurationError) as direct_error:
            get_watch_service(Request({"type": "http", "app": fresh}))
        assert direct_error.value is retained_error

        response = client.post(
            "/api/watches",
            json=_PRESERVATION_WATCH_QUERY,
        )
        assert response.status_code == 503
        assert response.json() == {"detail": str(retained_error)}
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "dibs-mvp",
            "config": "error",
            "watch_store": "memory",
            "watch_queue": "asyncio",
            "queue_readiness": "ready",
            "recovery_readiness": "unknown",
        }


@pytest.mark.parametrize(
    ("environment", "expected_settings"),
    _VALID_WATCH_SETTINGS_CASES,
)
def test_valid_watch_settings_preserve_service_state_without_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    environment: dict[str, str],
    expected_settings: WatchSettings,
) -> None:
    """**Validates: Requirements 3.4**"""

    _prepare_preservation_environment(monkeypatch, environment)
    redis_client = _PreservationRedisClient(reachable=False)
    monkeypatch.setattr(
        database,
        "create_redis_client",
        lambda _url: redis_client,
    )
    fresh = create_app()
    initial_queue = fresh.state.watch_queue

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        with TestClient(fresh) as client:
            assert fresh.state.watch_settings == expected_settings
            assert fresh.state.watch_settings_error is None
            assert isinstance(fresh.state.watch_repository, InMemoryWatchRepository)
            assert isinstance(fresh.state.watch_queue, AsyncioTaskQueue)
            assert fresh.state.watch_queue_mode == "asyncio"
            assert fresh.state.redis is None
            assert initial_queue._closed is True
            _assert_watch_service_settings(fresh, expected_settings)
            assert client.get("/health").json() == {
                "status": "ok",
                "service": "dibs-mvp",
                "config": "ok",
                "watch_store": "memory",
                "watch_queue": "asyncio",
                "queue_readiness": "ready",
                "recovery_readiness": "ready",
            }
            final_queue = fresh.state.watch_queue

    assert redis_client.ping_calls == 1
    assert redis_client.close_calls == 1
    assert final_queue._closed is True
    assert not [
        record
        for record in caplog.records
        if record.name == "backend.main" and record.levelno >= logging.ERROR
    ]


@pytest.mark.parametrize(
    ("worker_available", "expected_queue_type", "expected_mode"),
    [
        pytest.param(False, AsyncioTaskQueue, "asyncio", id="optional-worker-unavailable"),
        pytest.param(True, CeleryTaskQueue, "celery", id="optional-worker-available"),
    ],
)
def test_redis_available_preserves_repository_queue_and_client_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    worker_available: bool,
    expected_queue_type: type[Any],
    expected_mode: str,
) -> None:
    """**Validates: Requirements 3.4**"""

    environment = {
        "RESERVATION_TIMEZONE": "UTC",
        "REDIS_URL": "redis://preservation.example.test:6379/4",
        "WATCH_POLL_INTERVAL_SECONDS": "45",
        "WATCH_POLL_JITTER_SECONDS": "5",
        "WATCH_MAX_POLL_ATTEMPTS": "9",
    }
    expected_settings = WatchSettings(
        timezone_name="UTC",
        redis_url="redis://preservation.example.test:6379/4",
        poll_interval_seconds=45,
        poll_jitter_seconds=5,
        max_poll_attempts=9,
    )
    _prepare_preservation_environment(monkeypatch, environment)
    redis_client = _PreservationRedisClient(reachable=True)
    monkeypatch.setattr(
        database,
        "create_redis_client",
        lambda _url: redis_client,
    )

    module_name = "backend.workers.tasks.monitor_watch"
    fake_task = object()
    if worker_available:
        fake_worker_module = types.ModuleType(module_name)
        fake_worker_module.monitor_watch = fake_task
        monkeypatch.setitem(sys.modules, module_name, fake_worker_module)
    else:
        monkeypatch.setitem(sys.modules, module_name, None)

    fresh = create_app()
    initial_queue = fresh.state.watch_queue
    with TestClient(fresh) as client:
        assert fresh.state.watch_settings == expected_settings
        assert fresh.state.watch_settings_error is None
        assert redis_client.ping_calls == 1
        assert redis_client.close_calls == 0
        assert initial_queue._closed is True
        assert fresh.state.redis is redis_client
        assert isinstance(fresh.state.watch_repository, RedisWatchRepository)
        assert fresh.state.watch_repository._client is redis_client
        assert isinstance(fresh.state.watch_queue, expected_queue_type)
        assert fresh.state.watch_queue_mode == expected_mode
        if worker_available:
            assert fresh.state.watch_queue._task is fake_task
        _assert_watch_service_settings(fresh, expected_settings)
        # The injected `_PreservationRedisClient` implements only ping/aclose,
        # so the leadership check itself cannot complete: recovery degrades,
        # and asyncio mode still reports its own live open/closed state while
        # celery mode (no broker probe reached before that failure) is unknown.
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "dibs-mvp",
            "config": "ok",
            "watch_store": "redis",
            "watch_queue": expected_mode,
            "queue_readiness": "ready" if expected_mode == "asyncio" else "unknown",
            "recovery_readiness": "degraded",
        }

    assert redis_client.close_calls == 1


@pytest.mark.parametrize(
    ("environment", "expected_error"),
    [
        pytest.param(
            {"OPENAI_API_KEY": None},
            "OPENAI_API_KEY is not configured. Set it in the environment "
            "before starting the service.",
            id="missing-openai-api-key",
        ),
        pytest.param(
            {"OPENAI_MODEL": "not a model"},
            "Invalid OPENAI_MODEL name: 'not a model'. Expected an identifier "
            "such as 'gpt-4o-mini'.",
            id="invalid-openai-model",
        ),
    ],
)
def test_non_watch_settings_errors_do_not_become_watch_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    environment: dict[str, str | None],
    expected_error: str,
) -> None:
    """**Validates: Requirements 3.3, 3.4**"""

    _prepare_preservation_environment(monkeypatch, environment)
    redis_client = _PreservationRedisClient(reachable=False)
    monkeypatch.setattr(
        database,
        "create_redis_client",
        lambda _url: redis_client,
    )
    fresh = create_app()

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        with TestClient(fresh) as client:
            assert fresh.state.watch_settings == WatchSettings()
            assert fresh.state.watch_settings_error is None
            assert fresh.state.settings is None
            assert isinstance(fresh.state.settings_error, ConfigurationError)
            assert str(fresh.state.settings_error) == expected_error
            assert isinstance(fresh.state.watch_repository, InMemoryWatchRepository)
            assert fresh.state.watch_queue_mode == "asyncio"
            _assert_watch_service_settings(fresh, WatchSettings())
            assert client.get("/health").json() == {
                "status": "ok",
                "service": "dibs-mvp",
                "config": "error",
                "watch_store": "memory",
                "watch_queue": "asyncio",
                "queue_readiness": "ready",
                "recovery_readiness": "ready",
            }

    assert redis_client.ping_calls == 1
    assert redis_client.close_calls == 1
    assert not [
        record
        for record in caplog.records
        if record.name == "backend.main" and record.levelno >= logging.ERROR
    ]
