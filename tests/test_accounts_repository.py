"""AccountRepository + SessionRepository against a fake asyncpg pool (M5, Task 3).

No live PostgreSQL is started. The fake connection replicates just enough SQL
semantics -- unique email, lookup by email/id/token, delete by token/user -- to
prove the repositories' behavior, mirroring `tests/test_watch_history.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from backend.db.repositories.accounts import (
    AccountRepository,
    DuplicateEmailError,
    SessionRepository,
)
from backend.models.account import Session, StoredUser, User


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class _FakeConn:
    def __init__(self, store: dict[str, dict[Any, Any]]) -> None:
        self.store = store

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "INSERT INTO users" in query:
            email, password_hash = args
            if any(u["email"] == email for u in self.store["users"].values()):
                raise asyncpg.UniqueViolationError("duplicate key value: email")
            uid = uuid4()
            self.store["users"][uid] = {
                "id": uid,
                "email": email,
                "created_at": NOW,
                "password_hash": password_hash,
            }
            return [{"id": uid, "email": email, "created_at": NOW}]
        if "FROM users WHERE email" in query:
            (email,) = args
            return [u for u in self.store["users"].values() if u["email"] == email][:1]
        if "FROM users WHERE id" in query:
            (uid,) = args
            row = self.store["users"].get(uid)
            return [row] if row is not None else []
        if "FROM sessions WHERE token_hash" in query:
            (token_hash,) = args
            row = self.store["sessions"].get(token_hash)
            return [row] if row is not None else []
        raise AssertionError(f"unexpected fetch: {query}")

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO sessions" in query:
            token_hash, user_id, created_at, expires_at = args
            self.store["sessions"][token_hash] = {
                "token_hash": token_hash,
                "user_id": user_id,
                "created_at": created_at,
                "expires_at": expires_at,
            }
            return
        if "DELETE FROM sessions WHERE token_hash" in query:
            (token_hash,) = args
            self.store["sessions"].pop(token_hash, None)
            return
        if "DELETE FROM sessions WHERE user_id" in query:
            (user_id,) = args
            for th in [
                k for k, v in self.store["sessions"].items() if v["user_id"] == user_id
            ]:
                self.store["sessions"].pop(th, None)
            return
        raise AssertionError(f"unexpected execute: {query}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        raise NotImplementedError


class _AcquireCM:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.store: dict[str, dict[Any, Any]] = {"users": {}, "sessions": {}}
        self._conn = _FakeConn(self.store)

    def acquire(self) -> _AcquireCM:
        return _AcquireCM(self._conn)

    async def close(self) -> None:
        pass


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


# --- AccountRepository ------------------------------------------------------


def test_create_user_returns_a_public_user_without_a_hash() -> None:
    repo = AccountRepository(_FakePool())

    user = _run(repo.create_user("a@x.com", "argon2-hash"))

    assert isinstance(user, User)
    assert user.email == "a@x.com"
    assert isinstance(user.id, UUID)
    assert "password_hash" not in user.model_dump()


def test_create_user_with_a_duplicate_email_raises() -> None:
    repo = AccountRepository(_FakePool())
    _run(repo.create_user("dup@x.com", "hash-1"))

    with pytest.raises(DuplicateEmailError):
        _run(repo.create_user("dup@x.com", "hash-2"))


def test_get_by_email_returns_the_stored_user_with_its_hash() -> None:
    pool = _FakePool()
    repo = AccountRepository(pool)
    created = _run(repo.create_user("who@x.com", "the-hash"))

    stored = _run(repo.get_by_email("who@x.com"))

    assert isinstance(stored, StoredUser)
    assert stored.id == created.id
    assert stored.password_hash == "the-hash"
    # The public projection drops the hash.
    assert "password_hash" not in stored.to_public().model_dump()


def test_get_by_email_unknown_returns_none() -> None:
    repo = AccountRepository(_FakePool())
    assert _run(repo.get_by_email("nobody@x.com")) is None


def test_get_by_id_returns_public_user_or_none() -> None:
    repo = AccountRepository(_FakePool())
    created = _run(repo.create_user("id@x.com", "h"))

    assert _run(repo.get_by_id(created.id)) == created
    assert _run(repo.get_by_id(uuid4())) is None


# --- SessionRepository ------------------------------------------------------


def test_session_create_and_get_round_trip() -> None:
    repo = SessionRepository(_FakePool())
    uid = uuid4()

    created = _run(
        repo.create(
            token_hash="hash-abc",
            user_id=uid,
            created_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
    )
    assert isinstance(created, Session)

    fetched = _run(repo.get_by_token_hash("hash-abc"))
    assert fetched == created
    assert fetched.user_id == uid


def test_get_unknown_session_returns_none() -> None:
    repo = SessionRepository(_FakePool())
    assert _run(repo.get_by_token_hash("nope")) is None


def test_revoke_removes_the_session_and_is_idempotent() -> None:
    repo = SessionRepository(_FakePool())
    _run(
        repo.create(
            token_hash="h", user_id=uuid4(), created_at=NOW, expires_at=NOW
        )
    )

    _run(repo.revoke("h"))
    assert _run(repo.get_by_token_hash("h")) is None
    # Revoking again does not raise.
    _run(repo.revoke("h"))


def test_revoke_all_for_user_removes_only_that_users_sessions() -> None:
    pool = _FakePool()
    repo = SessionRepository(pool)
    mine, theirs = uuid4(), uuid4()
    _run(repo.create(token_hash="m1", user_id=mine, created_at=NOW, expires_at=NOW))
    _run(repo.create(token_hash="m2", user_id=mine, created_at=NOW, expires_at=NOW))
    _run(repo.create(token_hash="t1", user_id=theirs, created_at=NOW, expires_at=NOW))

    _run(repo.revoke_all_for_user(mine))

    assert _run(repo.get_by_token_hash("m1")) is None
    assert _run(repo.get_by_token_hash("m2")) is None
    assert _run(repo.get_by_token_hash("t1")) is not None
