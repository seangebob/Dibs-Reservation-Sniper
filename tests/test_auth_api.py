"""/api/auth/* routes: request/response shapes, header parsing, error mapping.

The AuthService logic is covered in test_auth_service.py; here a stub service is
injected so these tests exercise only the routing, models, bearer parsing, and
exception -> status mapping. The 503 accounts-unavailable path is covered in
test_milestone_5_preservation_baseline.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app, get_auth_service
from backend.models.account import User
from backend.services.auth_service import (
    EmailTakenError,
    InvalidCredentialsError,
    PasswordPolicyError,
)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _user(email: str) -> User:
    return User(id=uuid4(), email=email, created_at=NOW)


class _StubAuth:
    def __init__(self) -> None:
        self.logged_out: list[str] = []

    async def signup(self, email: str, password: str) -> tuple[User, str]:
        if email == "taken@x.com":
            raise EmailTakenError("An account with that email already exists.")
        if password == "weak":
            raise PasswordPolicyError("Password must be at least 8 characters.")
        return _user(email), "tok-signup"

    async def login(self, email: str, password: str) -> tuple[User, str]:
        if password != "right-password":
            raise InvalidCredentialsError()
        return _user(email), "tok-login"

    async def logout(self, raw_token: str) -> None:
        self.logged_out.append(raw_token)

    async def authenticate(self, raw_token: str | None) -> User | None:
        return _user("me@x.com") if raw_token == "good" else None


@pytest.fixture
def stub_client():
    stub = _StubAuth()
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: stub
    with TestClient(app) as client:
        yield client, stub
    app.dependency_overrides.clear()


def test_signup_returns_201_with_token_and_public_user(stub_client) -> None:
    client, _ = stub_client
    resp = client.post(
        "/api/auth/signup", json={"email": "a@x.com", "password": "hunter2-secret"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"] == "tok-signup"
    assert set(body["user"]) == {"id", "email", "created_at"}
    assert "password_hash" not in body["user"]


def test_signup_duplicate_maps_to_409(stub_client) -> None:
    client, _ = stub_client
    resp = client.post(
        "/api/auth/signup", json={"email": "taken@x.com", "password": "hunter2-secret"}
    )
    assert resp.status_code == 409


def test_signup_weak_password_maps_to_422(stub_client) -> None:
    client, _ = stub_client
    resp = client.post(
        "/api/auth/signup", json={"email": "a@x.com", "password": "weak"}
    )
    assert resp.status_code == 422


def test_login_returns_200_with_token(stub_client) -> None:
    client, _ = stub_client
    resp = client.post(
        "/api/auth/login", json={"email": "a@x.com", "password": "right-password"}
    )
    assert resp.status_code == 200
    assert resp.json()["token"] == "tok-login"


def test_login_bad_credentials_maps_to_401_generic(stub_client) -> None:
    client, _ = stub_client
    resp = client.post(
        "/api/auth/login", json={"email": "a@x.com", "password": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


def test_logout_with_bearer_revokes_and_returns_204(stub_client) -> None:
    client, stub = stub_client
    resp = client.post("/api/auth/logout", headers={"Authorization": "Bearer tok-xyz"})
    assert resp.status_code == 204
    assert stub.logged_out == ["tok-xyz"]


def test_logout_without_a_token_is_a_noop_204(stub_client) -> None:
    client, stub = stub_client
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert stub.logged_out == []


def test_me_with_a_valid_bearer_returns_the_user(stub_client) -> None:
    client, _ = stub_client
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@x.com"


def test_me_without_or_with_bad_token_is_401(stub_client) -> None:
    client, _ = stub_client
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
