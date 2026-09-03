"""Admission control on the paid orchestrator endpoints.

`/api/parse-and-book` and `/api/orchestrator/parse` call OpenAI, so every request
costs money and neither endpoint requires authentication. These tests prove the
spend ceiling holds, is keyed per caller, and counts every request rather than
only failures (the login throttle's rule).

A stub engine stands in for the orchestrator so no test spends a real API call.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.config import PromptThrottleSettings
from backend.main import create_app, get_orchestrator
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    OrchestratorRoute,
    ReservationIntent,
    VenueType,
)
from backend.services.throttle import RateLimitedError, SlidingWindowThrottle


PROMPT = {"prompt": "watch Cote for 4 on 2026-12-31 at 19:00"}
CLIENT_1 = {"X-Dibs-Client-Id": "visitor-1"}
CLIENT_2 = {"X-Dibs-Client-Id": "visitor-2"}


class _StubEngine:
    """Never contacts OpenAI; counts how often the paid path was reached."""

    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, prompt: str) -> ReservationIntent:
        self.calls += 1
        return ReservationIntent(
            status=IntentStatus.READY,
            route=OrchestratorRoute.BOOKING_SERVICE,
            action=IntentAction.SEARCH_AVAILABILITY,
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo-Cambridge, ON",
            party_size=4,
            date="2026-12-31",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
            missing_fields=[],
            clarification_question=None,
        )


@pytest.fixture
def client():
    app = create_app()
    engine = _StubEngine()
    app.state.prompt_throttle = SlidingWindowThrottle(
        max_events=3,
        window_seconds=300,
        on_limit=lambda: RateLimitedError("Too many prompt requests. Try again shortly."),
    )
    app.dependency_overrides[get_orchestrator] = lambda: engine
    with TestClient(app) as test_client:
        yield test_client, engine
    app.dependency_overrides.clear()


# --- the settings ----------------------------------------------------------


def test_settings_have_documented_defaults() -> None:
    settings = PromptThrottleSettings()

    assert settings.max_requests == 20
    assert settings.window_seconds == 300


def test_settings_read_bounded_values_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPT_THROTTLE_MAX_REQUESTS", "5")
    monkeypatch.setenv("PROMPT_THROTTLE_WINDOW_SECONDS", "60")

    settings = PromptThrottleSettings.from_environment()

    assert settings.max_requests == 5
    assert settings.window_seconds == 60


# --- the endpoints ---------------------------------------------------------


def test_parse_and_book_answers_429_past_the_ceiling(client) -> None:
    tc, engine = client
    for _ in range(3):
        assert tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1).status_code == 200

    resp = tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)

    assert resp.status_code == 429
    assert "Too many prompt requests" in resp.json()["detail"]


def test_a_throttled_request_never_reaches_the_paid_provider(client) -> None:
    """The whole point: the 429 must be spent before OpenAI is called."""

    tc, engine = client
    for _ in range(3):
        tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)
    assert engine.calls == 3

    tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)

    assert engine.calls == 3  # unchanged: the rejected request cost nothing


def test_every_request_counts_not_just_failures(client) -> None:
    """Unlike the login throttle, a *successful* call still consumes budget."""

    tc, _ = client
    for _ in range(3):
        assert tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1).status_code == 200

    assert tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1).status_code == 429


def test_the_ceiling_is_per_caller(client) -> None:
    tc, _ = client
    for _ in range(3):
        tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)

    # A different client id has its own untouched budget.
    assert tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_2).status_code == 200


def test_the_parse_only_endpoint_is_covered_too(client) -> None:
    """It calls OpenAI as well, so it cannot be a free bypass."""

    tc, _ = client
    for _ in range(3):
        assert tc.post("/api/orchestrator/parse", json=PROMPT, headers=CLIENT_1).status_code == 200

    assert tc.post("/api/orchestrator/parse", json=PROMPT, headers=CLIENT_1).status_code == 429


def test_the_two_paid_endpoints_share_one_budget(client) -> None:
    """Otherwise a caller doubles their spend by alternating endpoints."""

    tc, _ = client
    tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)
    tc.post("/api/orchestrator/parse", json=PROMPT, headers=CLIENT_1)
    tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)

    assert tc.post("/api/orchestrator/parse", json=PROMPT, headers=CLIENT_1).status_code == 429


def test_a_malformed_client_id_falls_back_rather_than_bypassing(client) -> None:
    """A malformed id resolves to None; the peer address must key it instead, so
    garbage headers are not a way around the ceiling."""

    tc, _ = client
    bad = {"X-Dibs-Client-Id": "has a space"}
    for _ in range(3):
        tc.post("/api/parse-and-book", json=PROMPT, headers=bad)

    assert tc.post("/api/parse-and-book", json=PROMPT, headers=bad).status_code == 429


def test_the_watch_routes_are_unaffected(client) -> None:
    """Only the paid endpoints are throttled -- M1-4 watch routes are free."""

    tc, _ = client
    for _ in range(6):
        tc.post("/api/parse-and-book", json=PROMPT, headers=CLIENT_1)

    assert tc.get("/api/watches", headers=CLIENT_1).status_code == 200
    assert tc.get("/api/watches/mine", headers=CLIENT_1).status_code == 200
    assert tc.get("/health").status_code == 200


def test_a_missing_throttle_leaves_the_endpoint_open() -> None:
    """Defensive: nothing on app.state must not break the route."""

    app = create_app()
    app.state.prompt_throttle = None
    app.dependency_overrides[get_orchestrator] = lambda: _StubEngine()
    with TestClient(app) as tc:
        for _ in range(5):
            assert tc.post("/api/parse-and-book", json=PROMPT).status_code == 200
    app.dependency_overrides.clear()
