"""Forgot password: an emailed single-use link that sets a new one (roadmap 3b)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config import AccountSettings
from backend.db.repositories.accounts import DuplicateEmailError
from backend.integrations.email import ComposedEmail
from backend.main import create_app
from backend.models.account import Session, StoredUser, User
from backend.services.auth_service import (
    AuthService,
    InvalidAccountLinkError,
    InvalidEmailError,
    PasswordPolicyError,
    TooManyAccountEmailsError,
    hash_token,
)
from backend.services.password import build_password_hasher


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class FakeAccounts:
    def __init__(self) -> None:
        self.by_email: dict[str, StoredUser] = {}
        self.by_id: dict[UUID, StoredUser] = {}

    async def create_user(self, email: str, password_hash: str) -> User:
        if email in self.by_email:
            raise DuplicateEmailError(email)
        stored = StoredUser(id=uuid4(), email=email, created_at=NOW, password_hash=password_hash)
        self.by_email[email] = stored
        self.by_id[stored.id] = stored
        return stored.to_public()

    async def get_by_email(self, email: str) -> StoredUser | None:
        return self.by_email.get(email)

    async def get_by_id(self, user_id: UUID) -> User | None:
        stored = self.by_id.get(user_id)
        return stored.to_public() if stored is not None else None

    async def update_password(self, user_id: UUID, password_hash: str) -> None:
        stored = self.by_id[user_id]
        updated = stored.model_copy(update={"password_hash": password_hash})
        self.by_id[user_id] = updated
        self.by_email[stored.email] = updated


class FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[str, Session] = {}

    async def create(self, *, token_hash: str, user_id: UUID, created_at: datetime, expires_at: datetime) -> Session:
        session = Session(token_hash=token_hash, user_id=user_id, created_at=created_at, expires_at=expires_at)
        self.rows[token_hash] = session
        return session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        return self.rows.get(token_hash)

    async def revoke(self, token_hash: str) -> None:
        self.rows.pop(token_hash, None)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        for key in [k for k, v in self.rows.items() if v.user_id == user_id]:
            del self.rows[key]


class FakeLinks:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def add(self, *, token_hash: str, user_id: UUID, purpose: str, expires_at: datetime) -> None:
        self.rows[token_hash] = {"user_id": user_id, "purpose": purpose, "expires_at": expires_at, "used_at": None}

    async def consume(self, token_hash: str, purpose: str, now: datetime) -> UUID | None:
        row = self.rows.get(token_hash)
        if row is None or row["purpose"] != purpose or row["used_at"] is not None or row["expires_at"] <= now:
            return None
        row["used_at"] = now
        return row["user_id"]

    async def cancel_unused(self, user_id: UUID, purpose: str, now: datetime) -> None:
        for row in self.rows.values():
            if row["user_id"] == user_id and row["purpose"] == purpose and row["used_at"] is None:
                row["used_at"] = now


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, ComposedEmail]] = []

    async def send(self, *, recipient: str, email: ComposedEmail) -> None:
        self.sent.append((recipient, email))


SETTINGS = AccountSettings(
    session_ttl_seconds=3_600,
    password_min_length=8,
    password_max_length=128,
    argon2_time_cost=1,
    argon2_memory_cost_kib=8_192,
    argon2_parallelism=1,
)


def _service(mailer: FakeMailer | None = None) -> tuple[AuthService, FakeAccounts, FakeSessions, FakeLinks, Clock]:
    accounts, sessions, links, clock = FakeAccounts(), FakeSessions(), FakeLinks(), Clock()
    service = AuthService(
        accounts=accounts,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        hasher=build_password_hasher(SETTINGS),
        settings=SETTINGS,
        clock=clock,
        links=links,  # type: ignore[arg-type]
        mailer=mailer,
        base_url="https://dibs.example.com/",
        throttle_clock=lambda: 0.0,
    )
    return service, accounts, sessions, links, clock


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def _token_from(email: ComposedEmail) -> str:
    marker = "/reset-password?token="
    return email.body[email.body.index(marker) + len(marker):].split()[0]


def _ask_for_reset(service: AuthService, email: str) -> None:
    _run(service.send_password_reset(service.prepare_password_reset(email)))


def test_a_reset_email_carries_a_single_use_link_for_one_hour() -> None:
    mailer = FakeMailer()
    service, _, _, links, _ = _service(mailer)
    _run(service.signup("diner@example.com", "old-password"))

    _ask_for_reset(service, "  Diner@Example.com ")

    [(recipient, email)] = mailer.sent
    assert recipient == "diner@example.com"
    assert "https://dibs.example.com/reset-password?token=" in email.body
    assert "expires in 1 hour" in email.body
    token = _token_from(email)
    [(stored_hash, row)] = links.rows.items()
    assert stored_hash == hash_token(token)
    assert row["purpose"] == "password_reset"
    assert row["expires_at"] == NOW + timedelta(hours=1)


def test_an_unknown_address_gets_no_email_and_no_error() -> None:
    mailer = FakeMailer()
    service, _, _, links, _ = _service(mailer)

    _ask_for_reset(service, "nobody@example.com")

    assert mailer.sent == [] and links.rows == {}


def test_a_malformed_address_is_rejected_up_front() -> None:
    service, *_ = _service(FakeMailer())

    with pytest.raises(InvalidEmailError):
        service.prepare_password_reset("not-an-email")


def test_one_address_gets_three_reset_emails_an_hour() -> None:
    service, *_ = _service(FakeMailer())
    for _ in range(3):
        service.prepare_password_reset("diner@example.com")

    with pytest.raises(TooManyAccountEmailsError):
        service.prepare_password_reset("Diner@example.com")


def test_using_the_link_sets_the_password_and_signs_out_everywhere_else() -> None:
    mailer = FakeMailer()
    service, _, sessions, _, _ = _service(mailer)
    _, old_session = _run(service.signup("diner@example.com", "old-password"))
    _ask_for_reset(service, "diner@example.com")

    user, new_session = _run(service.reset_password(_token_from(mailer.sent[0][1]), "new-password"))

    assert user.email == "diner@example.com"
    assert _run(service.authenticate(old_session)) is None
    assert _run(service.authenticate(new_session)) == user
    assert _run(service.login("diner@example.com", "new-password"))[0] == user
    assert len(sessions.rows) == 2  # the reset session and the login above


def test_a_reset_link_works_once_and_older_links_die_with_it() -> None:
    mailer = FakeMailer()
    service, *_ = _service(mailer)
    _run(service.signup("diner@example.com", "old-password"))
    _ask_for_reset(service, "diner@example.com")
    _ask_for_reset(service, "diner@example.com")
    older, newer = (_token_from(email) for _, email in mailer.sent)

    _run(service.reset_password(newer, "new-password"))

    for token in (newer, older):
        with pytest.raises(InvalidAccountLinkError):
            _run(service.reset_password(token, "another-password"))


def test_an_expired_link_does_nothing() -> None:
    mailer = FakeMailer()
    service, _, _, _, clock = _service(mailer)
    _run(service.signup("diner@example.com", "old-password"))
    _ask_for_reset(service, "diner@example.com")
    clock.now = NOW + timedelta(hours=1, seconds=1)

    with pytest.raises(InvalidAccountLinkError):
        _run(service.reset_password(_token_from(mailer.sent[0][1]), "new-password"))
    assert _run(service.login("diner@example.com", "old-password"))


def test_a_too_short_password_does_not_spend_the_link() -> None:
    mailer = FakeMailer()
    service, *_ = _service(mailer)
    _run(service.signup("diner@example.com", "old-password"))
    _ask_for_reset(service, "diner@example.com")
    token = _token_from(mailer.sent[0][1])

    with pytest.raises(PasswordPolicyError):
        _run(service.reset_password(token, "short"))
    assert _run(service.reset_password(token, "long-enough"))[0].email == "diner@example.com"


def test_without_a_mail_relay_the_link_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    service, *_ = _service(mailer=None)
    _run(service.signup("diner@example.com", "old-password"))

    with caplog.at_level(logging.INFO, logger="backend.services.auth_service"):
        _ask_for_reset(service, "diner@example.com")

    assert "https://dibs.example.com/reset-password?token=" in caplog.text


# --- the API -----------------------------------------------------------------


@pytest.fixture
def api() -> Any:
    app = create_app()
    mailer = FakeMailer()
    with TestClient(app) as client:
        service, *_ = _service(mailer)
        app.state.auth_service = service
        yield client, service, mailer


def test_forgot_answers_the_same_for_known_and_unknown_addresses(api: Any) -> None:
    client, service, mailer = api
    _run(service.signup("diner@example.com", "old-password"))

    known = client.post("/api/auth/password/forgot", json={"email": "diner@example.com"})
    unknown = client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})

    assert (known.status_code, known.content) == (unknown.status_code, unknown.content) == (202, b"")
    assert [recipient for recipient, _ in mailer.sent] == ["diner@example.com"]


def test_reset_signs_the_browser_in(api: Any) -> None:
    client, service, mailer = api
    _run(service.signup("diner@example.com", "old-password"))
    client.post("/api/auth/password/forgot", json={"email": "diner@example.com"})
    token = _token_from(mailer.sent[0][1])

    response = client.post("/api/auth/password/reset", json={"token": token, "password": "new-password"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "diner@example.com"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["email"] == "diner@example.com"

    again = client.post("/api/auth/password/reset", json={"token": token, "password": "new-password"})
    assert again.status_code == 400
    assert "already used" in again.json()["detail"]


def test_reset_rejects_a_short_password_with_the_policy_message(api: Any) -> None:
    client, *_ = api

    response = client.post("/api/auth/password/reset", json={"token": "x", "password": "short"})

    assert response.status_code == 422
    assert "at least 8" in response.json()["detail"]


# --- changing the password while signed in -----------------------------------

from backend.services.auth_service import (  # noqa: E402
    AuthenticationRequiredError,
    TooManyPasswordAttemptsError,
    WrongCurrentPasswordError,
)


def test_change_needs_the_current_password_and_signs_out_everywhere_else() -> None:
    service, *_ = _service()
    _, here = _run(service.signup("diner@example.com", "old-password"))
    _, elsewhere = _run(service.login("diner@example.com", "old-password"))

    user, fresh = _run(service.change_password(here, "old-password", "new-password"))

    assert user.email == "diner@example.com"
    assert _run(service.authenticate(here)) is None
    assert _run(service.authenticate(elsewhere)) is None
    assert _run(service.authenticate(fresh)) == user
    assert _run(service.login("diner@example.com", "new-password"))[0] == user


def test_a_wrong_current_password_changes_nothing() -> None:
    service, *_ = _service()
    _, session = _run(service.signup("diner@example.com", "old-password"))

    with pytest.raises(WrongCurrentPasswordError):
        _run(service.change_password(session, "not-it", "new-password"))

    assert _run(service.authenticate(session)) is not None
    assert _run(service.login("diner@example.com", "old-password"))


def test_change_rejects_a_too_short_new_password() -> None:
    service, *_ = _service()
    _, session = _run(service.signup("diner@example.com", "old-password"))

    with pytest.raises(PasswordPolicyError):
        _run(service.change_password(session, "old-password", "short"))


def test_change_needs_a_session() -> None:
    service, *_ = _service()

    with pytest.raises(AuthenticationRequiredError):
        _run(service.change_password(None, "old-password", "new-password"))


def test_five_wrong_guesses_lock_changes_for_a_while() -> None:
    service, *_ = _service()
    _, session = _run(service.signup("diner@example.com", "old-password"))
    for _ in range(5):
        with pytest.raises(WrongCurrentPasswordError):
            _run(service.change_password(session, "guess", "new-password"))

    with pytest.raises(TooManyPasswordAttemptsError):
        _run(service.change_password(session, "old-password", "new-password"))


def test_the_change_api_returns_a_fresh_session_and_a_wrong_password_is_not_a_401(api: Any) -> None:
    client, service, _ = api
    _, session = _run(service.signup("diner@example.com", "old-password"))
    auth = {"Authorization": f"Bearer {session}"}

    wrong = client.post(
        "/api/auth/password/change",
        headers=auth,
        json={"current_password": "nope", "new_password": "new-password"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "Your current password is incorrect."

    ok = client.post(
        "/api/auth/password/change",
        headers=auth,
        json={"current_password": "old-password", "new_password": "new-password"},
    )
    assert ok.status_code == 200
    fresh = ok.json()["token"]
    assert client.get("/api/auth/me", headers=auth).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200

    anonymous = client.post(
        "/api/auth/password/change",
        json={"current_password": "x", "new_password": "new-password"},
    )
    assert anonymous.status_code == 401
