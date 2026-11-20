"""Browser sessions live in an HttpOnly cookie, guarded against CSRF (roadmap 3b)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import AccountSettings, ConfigurationError
from backend.main import create_app
from test_password_reset import SETTINGS, FakeMailer, _run, _service

CREDS = {"email": "diner@example.com", "password": "a-password"}
CSRF = {"X-Dibs-CSRF": "1"}


def _client(settings: AccountSettings = SETTINGS, base_url: str = "http://testserver") -> Any:
    app = create_app()
    client = TestClient(app, base_url=base_url)
    client.__enter__()
    service, *_ = _service(FakeMailer())
    service._settings = settings
    app.state.auth_service = service
    return client


def _cookie_header(response: Any) -> str:
    [header] = [v for k, v in response.headers.multi_items() if k == "set-cookie"]
    return header


def test_signing_in_sets_an_httponly_lax_cookie_scoped_to_the_api() -> None:
    client = _client()

    response = client.post("/api/auth/signup", json=CREDS, headers=CSRF)

    cookie = _cookie_header(response)
    assert cookie.startswith("dibs_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api" in cookie
    assert "Max-Age=3600" in cookie
    assert "Secure" not in cookie  # plain http in "auto" mode


def test_the_cookie_alone_signs_the_browser_in() -> None:
    client = _client()
    client.post("/api/auth/signup", json=CREDS, headers=CSRF)

    me = client.get("/api/auth/me")

    assert me.status_code == 200
    assert me.json()["email"] == "diner@example.com"


def test_a_cookie_write_without_the_csrf_header_is_refused() -> None:
    client = _client()
    client.post("/api/auth/signup", json=CREDS, headers=CSRF)

    forged = client.post("/api/auth/logout")

    assert forged.status_code == 403
    assert "security header" in forged.json()["detail"]
    assert client.get("/api/auth/me").status_code == 200  # still signed in


def test_a_cookie_write_with_the_csrf_header_goes_through() -> None:
    client = _client()
    client.post("/api/auth/signup", json=CREDS, headers=CSRF)

    out = client.post("/api/auth/logout", headers=CSRF)

    assert out.status_code == 204
    assert 'dibs_session=""' in _cookie_header(out) or "Max-Age=0" in _cookie_header(out)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_the_session_not_just_the_cookie() -> None:
    client = _client()
    token = client.post("/api/auth/signup", json=CREDS, headers=CSRF).json()["token"]

    client.post("/api/auth/logout", headers=CSRF)

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_header_authenticated_and_anonymous_writes_need_no_csrf_header() -> None:
    client = _client()
    token = client.post("/api/auth/signup", json=CREDS, headers=CSRF).json()["token"]
    client.cookies.clear()

    with_bearer = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    anonymous = client.post("/api/auth/password/forgot", json={"email": "someone@example.com"})

    assert with_bearer.status_code == 204
    assert anonymous.status_code == 202


def test_the_cookie_is_secure_over_https_or_when_configured() -> None:
    over_https = _client(base_url="https://testserver")
    forced = _client(dataclasses.replace(SETTINGS, session_cookie_secure="true"))

    https_cookie = _cookie_header(over_https.post("/api/auth/signup", json=CREDS, headers=CSRF))
    forced_cookie = _cookie_header(forced.post("/api/auth/signup", json=CREDS, headers=CSRF))

    assert "Secure" in https_cookie
    assert "Secure" in forced_cookie


def test_reset_and_change_hand_the_browser_a_fresh_cookie() -> None:
    client = _client()
    client.post("/api/auth/signup", json=CREDS, headers=CSRF)
    before = client.cookies.get("dibs_session")

    changed = client.post(
        "/api/auth/password/change",
        json={"current_password": "a-password", "new_password": "another-password"},
        headers=CSRF,
    )

    assert changed.status_code == 200
    assert client.cookies.get("dibs_session") not in (None, before)
    assert client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("value", ["yes", "sometimes"])
def test_an_unknown_cookie_secure_setting_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SESSION_COOKIE_SECURE", value)

    with pytest.raises(ConfigurationError, match="SESSION_COOKIE_SECURE"):
        AccountSettings.from_environment()
