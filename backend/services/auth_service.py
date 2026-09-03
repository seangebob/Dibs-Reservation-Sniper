"""Authentication service for Milestone 5 accounts.

Owns the security-sensitive rules that sit above the repositories: email
normalization, password policy, generic non-enumerating login failures with
constant-time behavior for unknown emails, and opaque bearer sessions whose raw
token is returned once and only ever stored as a sha256 hash.

Raises domain errors (below); the route layer (Task 5) maps them to HTTP. Like
the repositories, it never contacts Redis or the fenced polling protocol.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.config import AccountSettings
from backend.db.repositories.accounts import (
    AccountRepository,
    DuplicateEmailError,
    SessionRepository,
)
from backend.models.account import User
from backend.services.password import PasswordHasherService


__all__ = [
    "AuthService",
    "AuthError",
    "AuthValidationError",
    "AuthenticationRequiredError",
    "EmailTakenError",
    "InvalidCredentialsError",
    "InvalidEmailError",
    "PasswordPolicyError",
]


class AuthError(Exception):
    """Base class for auth domain errors."""


class EmailTakenError(AuthError):
    """Signup for an email that already has an account (-> 409)."""


class InvalidCredentialsError(AuthError):
    """Unknown email or wrong password on login (-> 401). Deliberately does not
    distinguish the two, so it cannot be used to enumerate accounts."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class AuthenticationRequiredError(AuthError):
    """A route requiring a valid session got none (-> 401)."""

    def __init__(self) -> None:
        super().__init__("Authentication required.")


class AuthValidationError(AuthError):
    """A malformed signup input (-> 422). One base so the route layer maps every
    input-validation failure with a single handler."""


class InvalidEmailError(AuthValidationError):
    """A malformed email at signup (-> 422)."""


class PasswordPolicyError(AuthValidationError):
    """A password failing the length policy at signup (-> 422)."""


# A deliberately minimal shape check -- not full RFC 5322. Real deliverability
# is out of scope this milestone (no email service); this only keeps obvious
# garbage out of storage.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Clock = Callable[[], datetime]


def hash_token(raw_token: str) -> str:
    """sha256 hex of a bearer token. Only this hash is ever persisted, so a DB
    leak yields no usable credential."""

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class AuthService:
    """Signup / login / logout / authenticate over the account repositories."""

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        sessions: SessionRepository,
        hasher: PasswordHasherService,
        settings: AccountSettings,
        clock: Clock | None = None,
    ) -> None:
        self._accounts = accounts
        self._sessions = sessions
        self._hasher = hasher
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    async def signup(self, email: str, password: str) -> tuple[User, str]:
        """Create an account and open a session. Returns (public user, raw token).

        Validates before writing, so a weak password or bad email never creates
        an account (Requirement 1.6).
        """

        normalized = self._normalize_email(email)
        self._check_password(password)
        password_hash = self._hasher.hash(password)
        try:
            user = await self._accounts.create_user(normalized, password_hash)
        except DuplicateEmailError as exc:
            raise EmailTakenError(
                "An account with that email already exists."
            ) from exc
        token = await self._issue_session(user.id)
        return user, token

    async def login(self, email: str, password: str) -> tuple[User, str]:
        """Verify credentials and open a session. Returns (public user, raw token).

        An unknown email still spends argon2 time (dummy verify) so it is
        indistinguishable from a wrong password by latency, and both failures
        raise the same generic error (Requirement 1.4).
        """

        normalized = self._normalize_email(email)
        stored = await self._accounts.get_by_email(normalized)
        if stored is None:
            self._hasher.dummy_verify(password)
            raise InvalidCredentialsError()
        if not self._hasher.verify(stored.password_hash, password):
            raise InvalidCredentialsError()
        token = await self._issue_session(stored.id)
        return stored.to_public(), token

    async def logout(self, raw_token: str) -> None:
        """Revoke the presented session. Idempotent (Requirement 2.3)."""

        await self._sessions.revoke(hash_token(raw_token))

    async def authenticate(self, raw_token: str | None) -> User | None:
        """Resolve a bearer token to its user, or None when missing, unknown, or
        expired -- never raising, so callers treat those as anonymous (Req 2.2)."""

        if not raw_token:
            return None
        session = await self._sessions.get_by_token_hash(hash_token(raw_token))
        if session is None or session.expires_at <= self._clock():
            return None
        return await self._accounts.get_by_id(session.user_id)

    # -- internals ----------------------------------------------------------

    async def _issue_session(self, user_id: UUID) -> str:
        raw = secrets.token_urlsafe(32)
        now = self._clock()
        expires_at = now + timedelta(seconds=self._settings.session_ttl_seconds)
        await self._sessions.create(
            token_hash=hash_token(raw),
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
        )
        return raw

    def _normalize_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise InvalidEmailError("Enter a valid email address.")
        return normalized

    def _check_password(self, password: str) -> None:
        if len(password) < self._settings.password_min_length:
            raise PasswordPolicyError(
                f"Password must be at least {self._settings.password_min_length} "
                "characters."
            )
        if len(password) > self._settings.password_max_length:
            raise PasswordPolicyError(
                f"Password must be at most {self._settings.password_max_length} "
                "characters."
            )
