"""The optional auth lens: current_user (anonymous on anything but a valid
bearer, never an error) and require_user (401 otherwise).

These dependencies aren't attached to a real route until Task 7, so we mount
two probe routes on a throwaway app and drive a stub AuthService via
app.state -- exactly how main.py exposes the service to current_user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import current_user, require_user
from backend.models.account import User
from backend.services.auth_service import AuthenticationRequiredError


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
ME = User(id=uuid4(), email="me@x.com", created_at=NOW)


class _StubAuth:
    """authenticate() returns ME only for the "good" token; every other case
    (None, unknown, expired) is None -- the anonymous outcome."""

    async def authenticate(self, raw_token: str | None) -> User | None:
        return ME if raw_token == "good" else None


def _app(*, with_service: bool) -> FastAPI:
    app = FastAPI()
    app.state.auth_service = _StubAuth() if with_service else None

    @app.get("/optional")
    async def optional(user: User | None = Depends(current_user)) -> dict:
        return {"email": user.email if user else None}

    @app.get("/required")
    async def required(user: User = Depends(require_user)) -> dict:
        return {"email": user.email}

    app.add_exception_handler(
        AuthenticationRequiredError,
        lambda request, exc: _json_401(),
    )
    return app


def _json_401():
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=401, content={"detail": "Authentication required."})


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(with_service=True))


def test_valid_bearer_authenticates_on_an_optional_route(client: TestClient) -> None:
    resp = client.get("/optional", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@x.com"


def test_missing_token_is_anonymous_not_an_error(client: TestClient) -> None:
    resp = client.get("/optional")
    assert resp.status_code == 200
    assert resp.json()["email"] is None


def test_bad_or_expired_token_is_anonymous_not_an_error(client: TestClient) -> None:
    # The stub returns None for anything but "good" -- unknown and expired alike.
    resp = client.get("/optional", headers={"Authorization": "Bearer expired"})
    assert resp.status_code == 200
    assert resp.json()["email"] is None


def test_optional_route_is_anonymous_when_accounts_are_unavailable() -> None:
    # No auth_service on state (Postgres off): still anonymous, never 503.
    client = TestClient(_app(with_service=False))
    resp = client.get("/optional", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json()["email"] is None


def test_required_route_401s_without_a_valid_token(client: TestClient) -> None:
    assert client.get("/required").status_code == 401
    assert (
        client.get("/required", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )


def test_required_route_passes_with_a_valid_token(client: TestClient) -> None:
    resp = client.get("/required", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@x.com"
