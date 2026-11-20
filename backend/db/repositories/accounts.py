"""PostgreSQL repositories for accounts and bearer sessions (Milestone 5).

Thin data-access over the asyncpg pool, mirroring `WatchHistoryRepository`: each
method may raise on a Postgres failure -- swallowing/degrading is the wiring
layer's job (Task 5), not this layer's. Only argon2 password hashes and sha256
token hashes are read or written here; no recoverable secret is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from backend.db.postgres import PoolLike
from backend.models.account import Session, StoredUser, User


__all__ = [
    "AccountLinkRepository",
    "AccountRepository",
    "DuplicateEmailError",
    "SessionRepository",
]


class DuplicateEmailError(Exception):
    """Raised by `AccountRepository.create_user` when the email already exists.

    A domain error so the auth service (Task 4) maps it to a non-enumerating
    409 without importing asyncpg's exception types.
    """


_INSERT_USER_SQL = """
INSERT INTO users (email, password_hash)
VALUES ($1, $2)
RETURNING id, email, created_at, email_verified_at IS NOT NULL AS email_verified
""".strip()

_SELECT_USER_BY_EMAIL_SQL = (
    "SELECT id, email, created_at, password_hash, "
    "email_verified_at IS NOT NULL AS email_verified FROM users WHERE email = $1"
)

_SELECT_USER_BY_ID_SQL = (
    "SELECT id, email, created_at, email_verified_at IS NOT NULL AS email_verified "
    "FROM users WHERE id = $1"
)

_SET_EMAIL_VERIFIED_SQL = (
    "UPDATE users SET email_verified_at = COALESCE(email_verified_at, $2), "
    "updated_at = now() WHERE id = $1"
)

_UPDATE_PASSWORD_SQL = (
    "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1"
)


def _user(row: Any) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        created_at=row["created_at"],
        email_verified=bool(row.get("email_verified", False)),
    )


class AccountRepository:
    """Creates, reads, and updates `users` rows."""

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
        return _user(rows[0])

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
            email_verified=bool(row.get("email_verified", False)),
        )

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Return the public account for `user_id`, or None."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_USER_BY_ID_SQL, user_id)
        if not rows:
            return None
        return _user(rows[0])

    async def set_email_verified(self, user_id: UUID, verified_at: datetime) -> None:
        """Mark the address confirmed (keeping the first confirmation time)."""

        async with self._pool.acquire() as conn:
            await conn.execute(_SET_EMAIL_VERIFIED_SQL, user_id, verified_at)

    async def update_password(self, user_id: UUID, password_hash: str) -> None:
        """Replace an account's argon2 hash."""

        async with self._pool.acquire() as conn:
            await conn.execute(_UPDATE_PASSWORD_SQL, user_id, password_hash)


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


_INSERT_LINK_SQL = """
INSERT INTO account_links (token_hash, user_id, purpose, expires_at)
VALUES ($1, $2, $3, $4)
""".strip()

#: Reads and spends a link in one statement, so two clicks can't both succeed.
_CONSUME_LINK_SQL = """
UPDATE account_links SET used_at = $3
WHERE token_hash = $1 AND purpose = $2 AND used_at IS NULL AND expires_at > $3
RETURNING user_id
""".strip()

_CANCEL_LINKS_SQL = """
UPDATE account_links SET used_at = $3
WHERE user_id = $1 AND purpose = $2 AND used_at IS NULL
""".strip()


class AccountLinkRepository:
    """Single-use emailed links (password reset, email confirmation).

    Only the sha256 of a link's token is stored; the raw token exists only in
    the email.
    """

    def __init__(self, pool: PoolLike) -> None:
        self._pool = pool

    async def add(
        self, *, token_hash: str, user_id: UUID, purpose: str, expires_at: datetime
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_LINK_SQL, token_hash, user_id, purpose, expires_at)

    async def consume(self, token_hash: str, purpose: str, now: datetime) -> UUID | None:
        """Use a link once: its account, or None if unknown, used, or expired."""

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_CONSUME_LINK_SQL, token_hash, purpose, now)
        if not rows:
            return None
        user_id: UUID = rows[0]["user_id"]
        return user_id

    async def cancel_unused(self, user_id: UUID, purpose: str, now: datetime) -> None:
        """Spend every outstanding link of one kind, e.g. older reset links."""

        async with self._pool.acquire() as conn:
            await conn.execute(_CANCEL_LINKS_SQL, user_id, purpose, now)
