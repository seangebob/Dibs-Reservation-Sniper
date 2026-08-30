"""Preservation baselines Milestone 4 must not violate.

These characterization tests document the exact pre-Milestone-4 contract of the
four surfaces Milestone 4 changes: watch-service signatures, the watch API's
scoping (or lack of it), the ``/health`` shape, and CORS. Every subsequent
Milestone 4 change is additive and ``None``/absent-safe by construction; that
promise is only meaningful if these baselines exist to catch a regression.

They intentionally overlap with existing suites in places -- ``test_api.py``
already asserts the ``/health`` payload shape, ``test_watch_api.py`` the watch
route shapes -- because the point of a preservation baseline is to survive a
future refactor of the tests those assertions live in. If Milestone 4 rewrites
``test_api.py`` for its own reasons, these files still hold the line.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from backend.api.routes.watches import (
    cancel_watch,
    create_watch,
    list_watches,
    read_watch,
)
from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
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


class _EmptyAdapter(MockBookingAdapter):
    """Never has availability, so any created watch stays ACTIVE.

    Duplicated from ``test_watch_api.py`` on purpose: this baseline must not
    inherit test infrastructure whose own signatures Milestone 4 will change.
    """

    async def search_availability(self, query):  # noqa: ANN001
        return []


@pytest.fixture
def client():
    app = create_app()
    service = WatchService(
        InMemoryWatchRepository(), _EmptyAdapter(), RecordingTaskQueue()
    )
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Requirement 6.2: WatchService signatures have no owner concept today.
# ---------------------------------------------------------------------------


def test_watch_service_create_signature_has_no_owner_parameter() -> None:
    parameters = inspect.signature(WatchService.create).parameters

    assert set(parameters) == {"self", "query", "auto_book"}
    assert parameters["auto_book"].default is False


def test_watch_service_cancel_signature_has_no_owner_parameter() -> None:
    parameters = inspect.signature(WatchService.cancel).parameters

    assert set(parameters) == {"self", "watch_id"}


def test_watch_service_poll_signatures_have_no_owner_parameter() -> None:
    once = inspect.signature(WatchService.poll_once).parameters
    window = inspect.signature(WatchService.poll_window).parameters

    assert set(once) == {"self", "watch_id"}
    assert set(window) == {"self", "watch_id", "window_id", "owner_id", "enforce_due"}


# ---------------------------------------------------------------------------
# Requirement 6.2: watch route signatures have no owner/client-id parameter.
# ---------------------------------------------------------------------------


def test_watch_route_signatures_have_no_owner_or_client_id_parameter() -> None:
    for endpoint in (create_watch, list_watches, read_watch, cancel_watch):
        parameters = inspect.signature(endpoint).parameters
        forbidden = {"owner", "owner_client_id", "client_id", "x_dibs_client_id"}
        assert not (forbidden & set(parameters)), (
            f"{endpoint.__name__} unexpectedly accepts one of {forbidden}"
        )


# ---------------------------------------------------------------------------
# Requirement 6.2: GET /api/watches is globally unscoped -- watches created
# without any ownership signal appear in the list, and no ``owner`` query
# parameter is honored today.
# ---------------------------------------------------------------------------


def test_list_watches_returns_every_watch_regardless_of_creator(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).json()["watch_id"]
    second = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-beta"}
    ).json()["watch_id"]
    # A creation with no client header at all also lands in the same list.
    third = client.post("/api/watches", json=QUERY).json()["watch_id"]

    body = client.get("/api/watches").json()
    ids = {watch["watch_id"] for watch in body}

    assert {first, second, third} <= ids


def test_list_watches_ignores_an_owner_query_parameter_today(
    client: TestClient,
) -> None:
    kept = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).json()["watch_id"]

    scoped = client.get("/api/watches?owner=someone-else").json()
    ids = {watch["watch_id"] for watch in scoped}

    # The pre-Milestone-4 route has no ``owner`` filter, so passing one has no
    # effect: the alpha watch is still visible even though it was created
    # under a different client id.
    assert kept in ids


# ---------------------------------------------------------------------------
# Requirement 6.4: /health has no history_readiness field today.
# ---------------------------------------------------------------------------


def test_health_payload_has_no_history_field(client: TestClient) -> None:
    body = client.get("/health").json()

    for forbidden in ("history_readiness", "postgres", "watch_history", "history"):
        assert forbidden not in body, (
            f"pre-Milestone-4 /health unexpectedly reports {forbidden!r}"
        )


def test_health_payload_top_level_keys_are_exactly_the_milestone_3_set(
    client: TestClient,
) -> None:
    body = client.get("/health").json()

    assert set(body.keys()) == {
        "status",
        "service",
        "config",
        "watch_store",
        "watch_queue",
        "queue_readiness",
        "recovery_readiness",
    }


# ---------------------------------------------------------------------------
# Requirement 5.1/5.2/6.1: no CORS middleware is installed today, so no CORS
# response headers are sent on any route -- for a preflight or a normal call.
# ---------------------------------------------------------------------------


CORS_RESPONSE_HEADERS = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
    "access-control-expose-headers",
    "access-control-max-age",
)


def test_health_response_carries_no_cors_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    for header in CORS_RESPONSE_HEADERS:
        assert header not in response.headers, (
            f"pre-Milestone-4 /health unexpectedly sent CORS header {header!r}"
        )


def test_watch_route_response_carries_no_cors_headers(client: TestClient) -> None:
    response = client.post(
        "/api/watches",
        json=QUERY,
        headers={"Origin": "http://localhost:3000"},
    )

    for header in CORS_RESPONSE_HEADERS:
        assert header not in response.headers, (
            f"pre-Milestone-4 POST /api/watches unexpectedly sent CORS header {header!r}"
        )


def test_preflight_options_request_is_not_answered_by_cors_middleware(
    client: TestClient,
) -> None:
    response = client.options(
        "/api/watches",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    # Without CORS middleware installed, an OPTIONS to a route that has no
    # explicit handler falls through to Starlette's 405 (or similar non-200)
    # and carries none of the browser-required Access-Control-* headers.
    for header in CORS_RESPONSE_HEADERS:
        assert header not in response.headers
