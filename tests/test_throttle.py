"""The best-effort failed-login throttle (Req 6.4).

Unit tests drive an injectable clock so the sliding window is exercised without
sleeping; the HTTP tests prove /api/auth/login answers 429 past the threshold
and that nothing else (signup, a successful login) is affected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app, get_auth_service
from backend.models.account import User
from backend.services.auth_service import InvalidCredentialsError
from backend.services.throttle import (
    SlidingWindowThrottle,
    TooManyLoginAttemptsError,
    is_internal_address,
    login_email_key,
    login_ip_key,
    throttle_key,
)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
CREDS = {"email": "a@x.com", "password": "hunter2-secret"}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(clock: _Clock, *, max_attempts: int = 3, window: int = 300):
    return SlidingWindowThrottle(
        max_events=max_attempts,
        window_seconds=window,
        on_limit=TooManyLoginAttemptsError,
        clock=clock,
    )


# --- the counter -----------------------------------------------------------


def test_failures_below_the_threshold_are_allowed() -> None:
    clock = _Clock()
    throttle = _throttle(clock)

    for _ in range(2):
        throttle.record("k")
    throttle.check("k")  # 2 of 3: still fine


def test_the_threshold_blocks_further_attempts() -> None:
    clock = _Clock()
    throttle = _throttle(clock)

    for _ in range(3):
        throttle.record("k")

    with pytest.raises(TooManyLoginAttemptsError):
        throttle.check("k")


def test_the_window_slides_so_old_failures_stop_counting() -> None:
    clock = _Clock()
    throttle = _throttle(clock, window=300)
    for _ in range(3):
        throttle.record("k")

    clock.advance(301)

    throttle.check("k")  # the whole window drained


def test_a_partially_drained_window_still_counts_recent_failures() -> None:
    clock = _Clock()
    throttle = _throttle(clock, window=300)
    throttle.record("k")
    throttle.record("k")
    clock.advance(200)
    throttle.record("k")  # 3 within the window -> blocked

    with pytest.raises(TooManyLoginAttemptsError):
        throttle.check("k")

    clock.advance(150)  # the first two aged out, one recent failure remains
    throttle.check("k")


def test_a_success_clears_the_window() -> None:
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(3):
        throttle.record("k")

    throttle.reset("k")

    throttle.check("k")


def test_keys_are_independent() -> None:
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(3):
        throttle.record("a@x.com|https://app")

    throttle.check("b@x.com|https://app")  # a different key


def test_a_drained_key_is_forgotten_rather_than_accumulating() -> None:
    clock = _Clock()
    throttle = _throttle(clock, window=300)
    throttle.record("k")

    clock.advance(301)
    throttle.check("k")

    assert throttle._failures == {}


def test_login_keys_ignore_origin_and_normalize_email() -> None:
    assert login_email_key("  A@X.com ") == "email:a@x.com"
    assert login_ip_key("203.0.113.9") == "ip:203.0.113.9"
    assert login_ip_key(None) == "ip:-"


def test_throttle_key_normalizes_email_and_missing_origin() -> None:
    assert throttle_key("  A@X.com ", "https://app") == "a@x.com|https://app"
    assert throttle_key("a@x.com", None) == "a@x.com|-"


# --- the route -------------------------------------------------------------


class _AlwaysFailsAuth:
    async def login(self, email: str, password: str) -> tuple[User, str]:
        raise InvalidCredentialsError()

    async def signup(self, email: str, password: str) -> tuple[User, str]:
        return User(id=uuid4(), email=email, created_at=NOW), "tok"


class _AlwaysSucceedsAuth:
    async def login(self, email: str, password: str) -> tuple[User, str]:
        return User(id=uuid4(), email=email, created_at=NOW), "tok"


@pytest.fixture
def failing_client():
    app = create_app()
    app.state.login_throttle = SlidingWindowThrottle(
        max_events=3, window_seconds=300, on_limit=TooManyLoginAttemptsError
    )
    app.dependency_overrides[get_auth_service] = lambda: _AlwaysFailsAuth()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_login_answers_429_once_the_threshold_is_passed(failing_client) -> None:
    for _ in range(3):
        assert failing_client.post("/api/auth/login", json=CREDS).status_code == 401

    resp = failing_client.post("/api/auth/login", json=CREDS)
    assert resp.status_code == 429
    assert "Too many failed login attempts" in resp.json()["detail"]


def test_the_throttle_does_not_block_a_different_account(failing_client) -> None:
    for _ in range(3):
        failing_client.post("/api/auth/login", json=CREDS)

    other = {"email": "b@x.com", "password": "hunter2-secret"}
    assert failing_client.post("/api/auth/login", json=other).status_code == 401


def test_the_throttle_never_blocks_signup(failing_client) -> None:
    for _ in range(3):
        failing_client.post("/api/auth/login", json=CREDS)

    assert failing_client.post("/api/auth/signup", json=CREDS).status_code == 201


def test_a_successful_login_clears_the_throttle() -> None:
    app = create_app()
    app.state.login_throttle = SlidingWindowThrottle(
        max_events=3, window_seconds=300, on_limit=TooManyLoginAttemptsError
    )
    failing, succeeding = _AlwaysFailsAuth(), _AlwaysSucceedsAuth()
    current = {"service": failing}
    app.dependency_overrides[get_auth_service] = lambda: current["service"]

    with TestClient(app) as client:
        for _ in range(2):
            client.post("/api/auth/login", json=CREDS)
        current["service"] = succeeding
        assert client.post("/api/auth/login", json=CREDS).status_code == 200

        # The window was cleared, so the next failures start from zero.
        current["service"] = failing
        for _ in range(3):
            assert client.post("/api/auth/login", json=CREDS).status_code == 401
    app.dependency_overrides.clear()


def test_login_works_normally_when_no_throttle_is_configured() -> None:
    """No PostgreSQL -> no throttle on app.state; the route must not care."""

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: _AlwaysFailsAuth()
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json=CREDS).status_code == 401
    app.dependency_overrides.clear()


def _login_app(email_max: int = 3, ip_max: int = 100):
    app = create_app()
    app.state.login_throttle = SlidingWindowThrottle(
        max_events=email_max, window_seconds=300, on_limit=TooManyLoginAttemptsError
    )
    app.state.login_ip_throttle = SlidingWindowThrottle(
        max_events=ip_max, window_seconds=300, on_limit=TooManyLoginAttemptsError
    )
    return app


def test_a_made_up_origin_does_not_reset_the_email_limit() -> None:
    app = _login_app(email_max=3)
    app.dependency_overrides[get_auth_service] = lambda: _AlwaysFailsAuth()
    with TestClient(app) as client:
        for n in range(3):
            client.post(
                "/api/auth/login", json=CREDS, headers={"Origin": f"https://x{n}"}
            )
        resp = client.post(
            "/api/auth/login", json=CREDS, headers={"Origin": "https://fresh"}
        )
    assert resp.status_code == 429


def test_one_address_trying_many_emails_is_limited() -> None:
    app = _login_app(email_max=100, ip_max=3)
    app.dependency_overrides[get_auth_service] = lambda: _AlwaysFailsAuth()
    with TestClient(app) as client:
        for n in range(3):
            body = {"email": f"user{n}@x.com", "password": "hunter2-secret"}
            assert client.post("/api/auth/login", json=body).status_code == 401
        body = {"email": "someone-new@x.com", "password": "hunter2-secret"}
        assert client.post("/api/auth/login", json=body).status_code == 429


def test_a_successful_login_does_not_clear_the_address_limit() -> None:
    app = _login_app(email_max=100, ip_max=3)
    failing, succeeding = _AlwaysFailsAuth(), _AlwaysSucceedsAuth()
    current = {"service": failing}
    app.dependency_overrides[get_auth_service] = lambda: current["service"]
    with TestClient(app) as client:
        for n in range(2):
            body = {"email": f"victim{n}@x.com", "password": "hunter2-secret"}
            client.post("/api/auth/login", json=body)
        current["service"] = succeeding  # attacker logs into their own account
        assert client.post("/api/auth/login", json=CREDS).status_code == 200
        current["service"] = failing
        body = {"email": "victim9@x.com", "password": "hunter2-secret"}
        assert client.post("/api/auth/login", json=body).status_code == 401
        assert client.post("/api/auth/login", json=body).status_code == 429


def test_behind_an_untrusted_proxy_one_attacker_cannot_lock_everyone_out() -> None:
    """Without FORWARDED_ALLOW_IPS every request seems to come from the proxy's
    private address. The address limit must not then become one global limit."""

    app = _login_app(email_max=100, ip_max=3)
    failing, succeeding = _AlwaysFailsAuth(), _AlwaysSucceedsAuth()
    current = {"service": failing}
    app.dependency_overrides[get_auth_service] = lambda: current["service"]
    with TestClient(app, client=("10.0.0.5", 50000)) as client:
        for n in range(5):
            body = {"email": f"guess{n}@x.com", "password": "wrong-password"}
            client.post("/api/auth/login", json=body)
        current["service"] = succeeding  # a different, legitimate user
        assert client.post("/api/auth/login", json=CREDS).status_code == 200


def test_a_public_address_is_still_limited() -> None:
    app = _login_app(email_max=100, ip_max=3)
    app.dependency_overrides[get_auth_service] = lambda: _AlwaysFailsAuth()
    with TestClient(app, client=("8.8.8.8", 50000)) as client:
        for n in range(3):
            body = {"email": f"user{n}@x.com", "password": "hunter2-secret"}
            client.post("/api/auth/login", json=body)
        assert client.post("/api/auth/login", json=CREDS).status_code == 429


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("10.0.0.5", True),
        ("172.17.0.1", True),
        ("192.168.1.4", True),
        ("100.64.1.2", True),  # carrier-grade NAT range some hosts use internally
        ("127.0.0.1", True),
        ("fdaa::1", True),
        ("8.8.8.8", False),
        ("2001:4860:4860::8888", False),
        ("testclient", False),
        (None, False),
    ],
)
def test_internal_addresses_are_recognised(host, internal) -> None:
    assert is_internal_address(host) is internal
