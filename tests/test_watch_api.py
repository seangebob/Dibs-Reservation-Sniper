"""HTTP surface for watches, and the prompt path that opens one."""

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from backend.config import ConfigurationError, WatchSettings
from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
from backend.models.watch import Watch
from backend.orchestrator.router import PromptRouter
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    OrchestratorRoute,
    ReservationIntent,
    VenueType,
)
from backend.services.booking_service import BookingService
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


QUERY = {
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


class EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a created watch stays ACTIVE."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


@pytest.fixture
def queue() -> RecordingTaskQueue:
    return RecordingTaskQueue()


@pytest.fixture
def client(queue: RecordingTaskQueue):
    app = create_app()
    service = WatchService(InMemoryWatchRepository(), EmptyAdapter(), queue)
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_creating_a_watch_returns_201_and_queues_the_first_check(
    client: TestClient,
    queue: RecordingTaskQueue,
) -> None:
    response = client.post("/api/watches", json=QUERY)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["attempts"] == 0
    assert body["query"]["venue_name"] == "Cote"
    assert queue.dispatches == [(body["watch_id"], 0.0)]


def test_watches_can_be_listed_and_read_back(client: TestClient) -> None:
    watch_id = client.post("/api/watches", json=QUERY).json()["watch_id"]

    listed = client.get("/api/watches")
    read = client.get(f"/api/watches/{watch_id}")

    assert [record["watch_id"] for record in listed.json()] == [watch_id]
    assert read.status_code == 200
    assert read.json()["watch_id"] == watch_id


def test_listing_can_be_narrowed_to_active_watches(client: TestClient) -> None:
    first = client.post("/api/watches", json=QUERY).json()["watch_id"]
    client.post("/api/watches", json={**QUERY, "venue_name": "Bhima's Warung"})
    client.delete(f"/api/watches/{first}")

    assert len(client.get("/api/watches").json()) == 2
    assert len(client.get("/api/watches", params={"active_only": True}).json()) == 1


def test_cancelling_a_watch_marks_it_cancelled(client: TestClient) -> None:
    watch_id = client.post("/api/watches", json=QUERY).json()["watch_id"]

    response = client.delete(f"/api/watches/{watch_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["next_check_at"] is None


def test_unknown_watch_is_a_404(client: TestClient) -> None:
    assert client.get("/api/watches/watch_missing").status_code == 404
    assert client.delete("/api/watches/watch_missing").status_code == 404


def test_a_malformed_query_is_rejected_before_anything_is_queued(
    client: TestClient,
    queue: RecordingTaskQueue,
) -> None:
    response = client.post("/api/watches", json={**QUERY, "party_size": 0})

    assert response.status_code == 422
    assert queue.dispatches == []


def test_a_query_without_any_time_preference_is_rejected(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/watches",
        json={**QUERY, "preferred_time": None, "time_window": None},
    )

    assert response.status_code == 422


def test_auto_book_is_recorded_on_the_watch(client: TestClient) -> None:
    response = client.post(
        "/api/watches",
        json=QUERY,
        params={"auto_book": True},
    )

    assert response.json()["auto_book"] is True


def test_the_default_app_wires_a_real_watch_service_without_overrides() -> None:
    app = create_app()

    with TestClient(app) as client:
        created = client.post("/api/watches", json=QUERY)

    assert created.status_code == 201
    # The default in-process queue polls immediately, and the mock adapter has
    # a slot, so the watch is already resolved by the time we read it back.
    assert created.json()["watch_id"].startswith("watch_")


# --------------------------------------------------------------------------
# Property 1: Bug Condition - the watch-settings invariant on the route path
# --------------------------------------------------------------------------


class TrackingService(WatchService):
    """Fails the test if route work reaches the service at all."""

    def __init__(self, queue: RecordingTaskQueue) -> None:
        super().__init__(InMemoryWatchRepository(), EmptyAdapter(), queue)
        self.creates: list[object] = []

    async def create(self, query, *, auto_book: bool = False):  # noqa: ANN001, ANN201
        self.creates.append(query)
        return await super().create(query, auto_book=auto_book)


def _request_scope(app):  # noqa: ANN001, ANN202
    """A minimal Request stand-in whose `app.state` is the state under test."""

    return SimpleNamespace(app=app)


def test_missing_watch_settings_without_a_retained_error_is_an_invariant_failure(
    queue: RecordingTaskQueue,
) -> None:
    """C_route(X): no settings and no retained error must stop before route work.

    Without the invariant the route silently substitutes UTC for the configured
    reservation timezone, which shifts the past-date boundary by hours.
    """

    app = create_app()
    service = TrackingService(queue)
    app.state.watch_settings = None
    app.state.watch_settings_error = None
    app.state.watch_service = service

    with pytest.raises(RuntimeError):
        get_watch_service(_request_scope(app))

    assert service.creates == []
    assert queue.dispatches == []


def test_the_invariant_failure_is_not_a_configuration_error(
    queue: RecordingTaskQueue,
) -> None:
    """A missing-settings anomaly is a bug, not a user-facing 503."""

    app = create_app()
    service = TrackingService(queue)
    app.state.watch_settings = None
    app.state.watch_settings_error = None
    app.state.watch_service = service

    with pytest.raises(RuntimeError) as caught:
        get_watch_service(_request_scope(app))

    assert not isinstance(caught.value, ConfigurationError)


# --------------------------------------------------------------------------
# Property 1: Bug Condition - truthful monitoring-policy disclosure
# --------------------------------------------------------------------------


def _client_for(service: WatchService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_watch_service] = lambda: service
    return TestClient(app)


def test_a_deadline_capable_watch_advertises_the_deadline_policy(
    client: TestClient,
) -> None:
    response = client.post("/api/watches", json=QUERY)

    assert response.status_code == 201
    assert response.headers["X-Watch-Monitoring-Policy"] == "deadline"
    assert int(response.headers["X-Watch-Max-Availability-Checks"]) == (
        response.json()["max_attempts"]
    )
    assert "Warning" not in response.headers


def test_an_attempt_limited_watch_advertises_the_limitation() -> None:
    service = WatchService(
        InMemoryWatchRepository(),
        EmptyAdapter(),
        RecordingTaskQueue(),
        max_attempts=5,
    )

    with _client_for(service) as client:
        response = client.post("/api/watches", json=QUERY)

    assert response.status_code == 201
    assert response.headers["X-Watch-Monitoring-Policy"] == "attempt-limited"
    assert response.headers["X-Watch-Max-Availability-Checks"] == "5"
    assert response.json()["max_attempts"] == 5
    assert response.headers["Warning"].startswith("199")
    assert "may stop" in response.headers["Warning"]


def _watch_intent() -> ReservationIntent:
    return ReservationIntent(
        status=IntentStatus.READY,
        route=OrchestratorRoute.WATCH_SERVICE,
        action=IntentAction.CREATE_WATCH,
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date=QUERY["date"],
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
        missing_fields=[],
        clarification_question=None,
    )


def _router_for(max_attempts: int) -> PromptRouter:
    service = WatchService(
        InMemoryWatchRepository(),
        EmptyAdapter(),
        RecordingTaskQueue(),
        max_attempts=max_attempts,
    )
    return PromptRouter(BookingService(EmptyAdapter()), service)


def test_a_deadline_capable_router_message_keeps_the_original_promise() -> None:
    result = asyncio.run(_router_for(25_000).execute(_watch_intent()))

    assert "until a slot opens or the date passes" in result.message
    assert "up to" not in result.message


def test_an_attempt_limited_router_message_states_the_limitation() -> None:
    result = asyncio.run(_router_for(5).execute(_watch_intent()))

    assert "up to 5 availability checks" in result.message
    assert "may stop before" in result.message
    assert "until a slot opens or the date passes" not in result.message


def test_the_public_watch_json_gains_no_policy_fields(client: TestClient) -> None:
    """Policy disclosure is header-only; the body schema is unchanged."""

    body = client.post("/api/watches", json=QUERY).json()

    assert set(body) == set(Watch.model_fields)


# --------------------------------------------------------------------------
# Property 2: Preservation - retained configuration errors and timezone dates
# --------------------------------------------------------------------------


def _configured_app(
    queue: RecordingTaskQueue,
    *,
    settings: WatchSettings | None,
    error: ConfigurationError | None,
) -> tuple[Any, TrackingService]:
    """An app whose watch state is set directly, bypassing lifespan."""

    app = create_app()
    service = TrackingService(queue)
    app.state.watch_settings = settings
    app.state.watch_settings_error = error
    app.state.watch_service = service
    return app, service


def test_a_retained_configuration_error_is_raised_by_identity(
    queue: RecordingTaskQueue,
) -> None:
    retained = ConfigurationError("WATCH_POLL_INTERVAL_SECONDS must be an integer")
    app, service = _configured_app(queue, settings=None, error=retained)

    with pytest.raises(ConfigurationError) as caught:
        get_watch_service(_request_scope(app))

    assert caught.value is retained
    assert service.creates == []
    assert queue.dispatches == []


def test_the_retained_error_wins_even_when_settings_are_also_present(
    queue: RecordingTaskQueue,
) -> None:
    """Error precedence is checked before anything else, and stays that way."""

    retained = ConfigurationError("Unknown RESERVATION_TIMEZONE: Mars/Olympus_Mons")
    app, _ = _configured_app(queue, settings=WatchSettings(), error=retained)

    with pytest.raises(ConfigurationError) as caught:
        get_watch_service(_request_scope(app))

    assert caught.value is retained


def test_a_retained_configuration_error_becomes_a_503_before_route_work(
    queue: RecordingTaskQueue,
) -> None:
    retained = ConfigurationError("Invalid REDIS_URL: 'ftp://nope'")
    app, service = _configured_app(queue, settings=None, error=retained)

    response = TestClient(app).post("/api/watches", json=QUERY)

    assert response.status_code == 503
    assert response.json() == {"detail": str(retained)}
    assert service.creates == []
    assert queue.dispatches == []


def test_valid_settings_return_the_service_already_bound_to_the_app(
    queue: RecordingTaskQueue,
) -> None:
    app, service = _configured_app(queue, settings=WatchSettings(), error=None)

    assert get_watch_service(_request_scope(app)) is service


#: Deliberately far from UTC, so a UTC fallback would land on a different day
#: for part of every calendar day.
FAR_TIMEZONE = "Pacific/Kiritimati"


def _far_timezone_today() -> date:
    return datetime.now(ZoneInfo(FAR_TIMEZONE)).date()


def test_a_past_date_in_the_configured_timezone_is_422_with_no_service_call(
    queue: RecordingTaskQueue,
) -> None:
    settings = WatchSettings(timezone_name=FAR_TIMEZONE)
    app, service = _configured_app(queue, settings=settings, error=None)
    yesterday = _far_timezone_today() - timedelta(days=1)

    response = TestClient(app).post(
        "/api/watches",
        json={**QUERY, "date": yesterday.isoformat()},
    )

    assert response.status_code == 422
    assert service.creates == []
    assert queue.dispatches == []


@pytest.mark.parametrize(
    "days_ahead",
    [0, 1],
    ids=["today-in-configured-timezone", "tomorrow"],
)
def test_a_current_date_creates_normally_with_one_zero_delay_dispatch(
    queue: RecordingTaskQueue,
    days_ahead: int,
) -> None:
    settings = WatchSettings(timezone_name=FAR_TIMEZONE)
    app, service = _configured_app(queue, settings=settings, error=None)
    target = _far_timezone_today() + timedelta(days=days_ahead)

    response = TestClient(app).post(
        "/api/watches",
        json={**QUERY, "date": target.isoformat()},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert len(service.creates) == 1
    assert queue.dispatches == [(body["watch_id"], 0.0)]
