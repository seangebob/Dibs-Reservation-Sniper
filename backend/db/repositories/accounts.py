"""PostgreSQL repositories for accounts and bearer sessions (Milestone 5).

Thin data-access over the asyncpg pool, mirroring `WatchHistoryRepository`: each
method may raise on a Postgres failure -- swallowing/degrading is the wiring
layer's job (Task 5), not this layer's. Only argon2 password hashes and sha256
token hashes are read or written here; no recoverable secret is stored.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from backend.db.postgres import PoolLike
from backend.models.account import Session, StoredUser, User


__all__ = ["AccountRepository", "SessionRepository", "DuplicateEmailError"]


class DuplicateEmailError(Exception):
    """Raised by `AccountRepository.create_user` when the email already exists.

    A domain error so the auth service (Task 4) maps it to a non-enumerating
    409 without importing asyncpg's exception types.
    """


_INSERT_USER_SQL = """
INSERT INTO users (email, password_hash)
VALUES ($1, $2)
RETURNING id, email, created_at
""".strip()

_SELECT_USER_BY_EMAIL_SQL = (
    "SELECT id, email, created_at, password_hash FROM users WHERE email = $1"
)

_SELECT_USER_BY_ID_SQL = "SELECT id, email, created_at FROM users WHERE id = $1"


class AccountRepository:
    """Creates and reads `users` rows."""

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def create_user(self, email: str, password_hash: str) -> User:
        """Insert a new account and return its public view.

        `email` must already be normalized by the caller (Req 1.5). A unique
        violation becomes `DuplicateEmailError`.
        """

        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(_INSERT_USER_SQL, email, password_hash)
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateEmailError(email) from exc
        row = rows[0]
        return User(id=row["id"], email=row["email"], created_at=row["created_at"])

    async def get_by_email(self, email: str) -> StoredUser | None:
        """Return the account for `email` including its hash (for login), or None."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_USER_BY_EMAIL_SQL, email)
        if not rows:
            return None
        row = rows[0]
        return StoredUser(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            password_hash=row["password_hash"],
        )

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Return the public account for `user_id`, or None."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_USER_BY_ID_SQL, user_id)
        if not rows:
            return None
        row = rows[0]
        return User(id=row["id"], email=row["email"], created_at=row["created_at"])


_INSERT_SESSION_SQL = """
INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
VALUES ($1, $2, $3, $4)
""".strip()

_SELECT_SESSION_SQL = (
    "SELECT token_hash, user_id, created_at, expires_at "
    "FROM sessions WHERE token_hash = $1"
)

_DELETE_SESSION_SQL = "DELETE FROM sessions WHERE token_hash = $1"

_DELETE_SESSIONS_FOR_USER_SQL = "DELETE FROM sessions WHERE user_id = $1"


class SessionRepository:
    """Creates, reads, and revokes bearer sessions (by token hash)."""

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        token_hash: str,
        user_id: UUID,
        created_at: datetime,
        expires_at: datetime,
    ) -> Session:
        """Persist a session row. The raw token is never passed here -- only its
        hash -- so this layer cannot leak a usable credential."""

        async with self._pool.acquire() as conn:
            await conn.execute(
                _INSERT_SESSION_SQL, token_hash, user_id, created_at, expires_at
            )
        return Session(
            token_hash=token_hash,
            user_id=user_id,
            created_at=created_at,
            expires_at=expires_at,
        )

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session for `token_hash`, or None. Expiry is the caller's
        check (the service owns the clock), keeping this a pure data layer."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_SESSION_SQL, token_hash)
        if not rows:
            return None
        row = rows[0]
        return Session(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    async def revoke(self, token_hash: str) -> None:
        """Delete one session. Idempotent: revoking an absent token is a no-op."""

        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SESSION_SQL, token_hash)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Delete every session for a user (e.g. a future 'log out everywhere')."""

        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SESSIONS_FOR_USER_SQL, user_id)
