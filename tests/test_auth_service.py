"""AuthService: signup / login / logout / authenticate (Milestone 5, Task 4).

Uses in-memory fake repositories and the real (fast-param) argon2 hasher, so the
full credential round-trip is exercised without a live PostgreSQL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from backend.config import AccountSettings
from backend.db.repositories.accounts import DuplicateEmailError
from backend.models.account import Session, StoredUser, User
from backend.services.auth_service import (
    AuthService,
    EmailTakenError,
    InvalidCredentialsError,
    InvalidEmailError,
    PasswordPolicyError,
)
from backend.services.password import build_password_hasher


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _FakeAccounts:
    def __init__(self) -> None:
        self.by_email: dict[str, StoredUser] = {}
        self.by_id: dict[UUID, StoredUser] = {}

    async def create_user(self, email: str, password_hash: str) -> User:
        if email in self.by_email:
            raise DuplicateEmailError(email)
        stored = StoredUser(
            id=uuid4(), email=email, created_at=NOW, password_hash=password_hash
        )
        self.by_email[email] = stored
        self.by_id[stored.id] = stored
        return stored.to_public()

    async def get_by_email(self, email: str) -> StoredUser | None:
        return self.by_email.get(email)

    async def get_by_id(self, user_id: UUID) -> User | None:
        stored = self.by_id.get(user_id)
        return stored.to_public() if stored is not None else None


class _FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[str, Session] = {}

    async def create(
        self,
        *,
        token_hash: str,
        user_id: UUID,
        created_at: datetime,
        expires_at: datetime,
    ) -> Session:
        session = Session(
            token_hash=token_hash,
            user_id=user_id,
            created_at=created_at,
            expires_at=expires_at,
        )
        self.rows[token_hash] = session
        return session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        return self.rows.get(token_hash)

    async def revoke(self, token_hash: str) -> None:
        self.rows.pop(token_hash, None)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        for key in [k for k, v in self.rows.items() if v.user_id == user_id]:
            self.rows.pop(key, None)


def _service(clock: _Clock | None = None) -> tuple[AuthService, _FakeAccounts, _FakeSessions]:
    accounts = _FakeAccounts()
    sessions = _FakeSessions()
    settings = AccountSettings(
        session_ttl_seconds=3_600,
        password_min_length=8,
        password_max_length=128,
        argon2_time_cost=1,
        argon2_memory_cost_kib=8_192,
        argon2_parallelism=1,
    )
    service = AuthService(
        accounts=accounts,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        hasher=build_password_hasher(settings),
        settings=settings,
        clock=clock or _Clock(NOW),
    )
    return service, accounts, sessions


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


# --- signup -----------------------------------------------------------------


def test_signup_creates_account_and_a_working_session() -> None:
    service, _, _ = _service()

    user, token = _run(service.signup("a@x.com", "hunter2-secret"))

    assert user.email == "a@x.com"
    assert token  # raw token returned once
    assert _run(service.authenticate(token)) == user


def test_signup_normalizes_the_email() -> None:
    service, accounts, _ = _service()

    user, _ = _run(service.signup("  A@X.com  ", "hunter2-secret"))

    assert user.email == "a@x.com"
    assert "a@x.com" in accounts.by_email


def test_signup_duplicate_email_raises_email_taken() -> None:
    service, _, _ = _service()
    _run(service.signup("dup@x.com", "hunter2-secret"))

    with pytest.raises(EmailTakenError):
        _run(service.signup("DUP@x.com", "another-secret"))


def test_signup_short_password_raises_and_creates_nothing() -> None:
    service, accounts, _ = _service()

    with pytest.raises(PasswordPolicyError):
        _run(service.signup("new@x.com", "short"))
    assert accounts.by_email == {}


def test_signup_malformed_email_raises() -> None:
    service, _, _ = _service()

    with pytest.raises(InvalidEmailError):
        _run(service.signup("not-an-email", "hunter2-secret"))


# --- login ------------------------------------------------------------------


def test_login_succeeds_with_correct_credentials() -> None:
    service, _, _ = _service()
    created, _ = _run(service.signup("me@x.com", "hunter2-secret"))

    user, token = _run(service.login("ME@x.com", "hunter2-secret"))

    assert user == created
    assert _run(service.authenticate(token)) == created


def test_login_unknown_email_and_wrong_password_fail_identically() -> None:
    service, _, _ = _service()
    _run(service.signup("known@x.com", "hunter2-secret"))

    with pytest.raises(InvalidCredentialsError) as unknown:
        _run(service.login("nobody@x.com", "hunter2-secret"))
    with pytest.raises(InvalidCredentialsError) as wrong:
        _run(service.login("known@x.com", "wrong-password"))

    # Same generic message either way -- no account enumeration.
    assert str(unknown.value) == str(wrong.value) == "Invalid email or password."


# --- logout + authenticate --------------------------------------------------


def test_logout_revokes_the_session() -> None:
    service, _, _ = _service()
    _, token = _run(service.signup("out@x.com", "hunter2-secret"))
    assert _run(service.authenticate(token)) is not None

    _run(service.logout(token))
    assert _run(service.authenticate(token)) is None


def test_logout_is_idempotent_for_an_unknown_token() -> None:
    service, _, _ = _service()
    _run(service.logout("never-issued"))  # must not raise


def test_authenticate_returns_none_for_missing_or_bogus_tokens() -> None:
    service, _, _ = _service()

    assert _run(service.authenticate(None)) is None
    assert _run(service.authenticate("")) is None
    assert _run(service.authenticate("not-a-real-token")) is None


def test_authenticate_returns_none_once_the_session_has_expired() -> None:
    clock = _Clock(NOW)
    service, _, _ = _service(clock=clock)
    _, token = _run(service.signup("exp@x.com", "hunter2-secret"))

    assert _run(service.authenticate(token)) is not None
    clock.advance(3_601)  # past the 3600s TTL
    assert _run(service.authenticate(token)) is None
