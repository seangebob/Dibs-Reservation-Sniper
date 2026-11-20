"""Authentication service for Milestone 5 accounts.

Owns the security-sensitive rules that sit above the repositories: email
normalization, password policy, generic non-enumerating login failures with
constant-time behavior for unknown emails, and opaque bearer sessions whose raw
token is returned once and only ever stored as a sha256 hash.

Raises domain errors (below); the route layer (Task 5) maps them to HTTP. Like
the repositories, it never contacts Redis or the fenced polling protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID
from typing import Any, TypeVar

from backend.config import DEFAULT_DASHBOARD_BASE_URL, AccountSettings
from backend.db.repositories.accounts import (
    AccountLinkRepository,
    AccountRepository,
    DuplicateEmailError,
    SessionRepository,
)
from backend.integrations.email import ComposedEmail, SmtpSender
from backend.models.account import User
from backend.services.password import PasswordHasherService
from backend.services.throttle import RateLimitedError, SlidingWindowThrottle


__all__ = [
    "AuthService",
    "AuthError",
    "AuthValidationError",
    "AuthenticationRequiredError",
    "EmailTakenError",
    "InvalidCredentialsError",
    "InvalidEmailError",
    "InvalidAccountLinkError",
    "PasswordPolicyError",
    "TooManyAccountEmailsError",
    "TooManyPasswordAttemptsError",
    "WrongCurrentPasswordError",
    "hash_token",
    "normalize_email",
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


class WrongCurrentPasswordError(AuthValidationError):
    """The current password given to change it is wrong (-> 422, not 401: the
    session is fine, and a 401 would sign the browser out)."""

    def __init__(self) -> None:
        super().__init__("Your current password is incorrect.")


class TooManyPasswordAttemptsError(RateLimitedError):
    """Too many wrong current passwords for one account (-> 429)."""

    def __init__(self) -> None:
        super().__init__("Too many wrong passwords. Try again in a few minutes.")


class InvalidAccountLinkError(AuthError):
    """An emailed link that is unknown, used, or expired (-> 400)."""

    def __init__(self) -> None:
        super().__init__(
            "This link is invalid, already used, or expired. Ask for a new one."
        )


class TooManyAccountEmailsError(RateLimitedError):
    """Too many account emails for one address this hour (-> 429)."""

    def __init__(self) -> None:
        super().__init__("Too many emails for this address. Try again in an hour.")


logger = logging.getLogger(__name__)

#: A reset link is a key to the account, so it lives briefly.
PASSWORD_RESET_TTL = timedelta(hours=1)
PURPOSE_PASSWORD_RESET = "password_reset"
#: Account emails (reset links) one address may be sent per hour.
ACCOUNT_EMAILS_PER_HOUR = 3


def compose_password_reset(link: str) -> ComposedEmail:
    return ComposedEmail(
        subject="Reset your Dibs password",
        body=(
            "Someone asked to reset the password for your Dibs account. To choose "
            f"a new one, open this link:\n\n{link}\n\n"
            "It works once and expires in 1 hour. If you didn't ask for this, "
            "ignore this email; your password stays the same.\n\n-- Dibs\n"
        ),
    )


# A deliberately minimal shape check -- not full RFC 5322. Real deliverability
# is out of scope this milestone (no email service); this only keeps obvious
# garbage out of storage.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Clock = Callable[[], datetime]
T = TypeVar("T")


def normalize_email(email: str) -> str:
    """Trim and lowercase an address, or raise `InvalidEmailError` if malformed."""

    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise InvalidEmailError("Enter a valid email address.")
    return normalized


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
        links: AccountLinkRepository | None = None,
        mailer: SmtpSender | None = None,
        base_url: str = DEFAULT_DASHBOARD_BASE_URL,
        throttle_clock: Callable[[], float] | None = None,
    ) -> None:
        """`links` enables emailed links (password reset); `mailer` sends them,
        and without one the link is logged so local development can use it."""

        self._accounts = accounts
        self._sessions = sessions
        self._hasher = hasher
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._links = links
        self._mailer = mailer
        self._base_url = base_url.rstrip("/")
        self._email_throttle = SlidingWindowThrottle(
            max_events=ACCOUNT_EMAILS_PER_HOUR,
            window_seconds=3_600,
            on_limit=TooManyAccountEmailsError,
            clock=throttle_clock,
        )
        # Wrong current passwords per account: a stolen session must not be
        # enough to guess the password and take the account over.
        self._change_throttle = SlidingWindowThrottle(
            max_events=5,
            window_seconds=300,
            on_limit=TooManyPasswordAttemptsError,
            clock=throttle_clock,
        )
        # Bound Argon2's CPU/memory use while keeping the API loop responsive.
        self._password_slots = asyncio.Semaphore(4)

    async def _password_work(self, operation: Callable[..., T], *args: Any) -> T:
        async with self._password_slots:
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled await cannot stop a running thread. Keep its
                # slot until it finishes rather than admitting extra hashers.
                try:
                    await task
                finally:
                    raise

    async def signup(self, email: str, password: str) -> tuple[User, str]:
        """Create an account and open a session. Returns (public user, raw token).

        Validates before writing, so a weak password or bad email never creates
        an account (Requirement 1.6).
        """

        normalized = self._normalize_email(email)
        self._check_password(password)
        password_hash = await self._password_work(self._hasher.hash, password)
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
            await self._password_work(self._hasher.dummy_verify, password)
            raise InvalidCredentialsError()
        if not await self._password_work(self._hasher.verify, stored.password_hash, password):
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

    # -- password change -----------------------------------------------------

    async def change_password(
        self, raw_token: str | None, current_password: str, new_password: str
    ) -> tuple[User, str]:
        """Change a signed-in account's password.

        Needs the current password. Every session -- including this one -- is
        signed out and a fresh one is returned for this browser, so anyone
        else signed in to the account is locked out.
        """

        user = await self.authenticate(raw_token)
        if user is None:
            raise AuthenticationRequiredError()
        key = f"change:{user.id}"
        self._change_throttle.check(key)
        self._check_password(new_password)
        stored = await self._accounts.get_by_email(user.email)
        if stored is None:
            raise AuthenticationRequiredError()
        if not await self._password_work(
            self._hasher.verify, stored.password_hash, current_password
        ):
            self._change_throttle.record(key)
            raise WrongCurrentPasswordError()
        password_hash = await self._password_work(self._hasher.hash, new_password)
        await self._accounts.update_password(user.id, password_hash)
        await self._sessions.revoke_all_for_user(user.id)
        self._change_throttle.reset(key)
        return user, await self._issue_session(user.id)

    # -- password reset ------------------------------------------------------

    def prepare_password_reset(self, email: str) -> str:
        """Validate and rate-limit a reset request; returns the normalized email.

        Cheap and identical for every address, so the response can't reveal
        whether an account exists. `send_password_reset` does the rest after
        the response has gone out.
        """

        normalized = self._normalize_email(email)
        key = f"reset:{normalized}"
        self._email_throttle.check(key)
        self._email_throttle.record(key)
        return normalized

    async def send_password_reset(self, normalized_email: str) -> None:
        """Email a reset link if the account exists; otherwise do nothing."""

        if self._links is None:
            return
        stored = await self._accounts.get_by_email(normalized_email)
        if stored is None:
            return
        raw = secrets.token_urlsafe(32)
        await self._links.add(
            token_hash=hash_token(raw),
            user_id=stored.id,
            purpose=PURPOSE_PASSWORD_RESET,
            expires_at=self._clock() + PASSWORD_RESET_TTL,
        )
        link = f"{self._base_url}/reset-password?token={raw}"
        await self._deliver(stored.email, compose_password_reset(link), link)

    async def reset_password(self, raw_token: str, new_password: str) -> tuple[User, str]:
        """Use a reset link: set the password, sign out everywhere, sign in here.

        The new password is checked before the link is spent, so a too-short
        password doesn't waste the link. Every older reset link stops working.
        """

        self._check_password(new_password)
        if self._links is None:
            raise InvalidAccountLinkError()
        now = self._clock()
        user_id = await self._links.consume(
            hash_token(raw_token.strip()), PURPOSE_PASSWORD_RESET, now
        )
        if user_id is None:
            raise InvalidAccountLinkError()
        password_hash = await self._password_work(self._hasher.hash, new_password)
        await self._accounts.update_password(user_id, password_hash)
        await self._sessions.revoke_all_for_user(user_id)
        await self._links.cancel_unused(user_id, PURPOSE_PASSWORD_RESET, now)
        user = await self._accounts.get_by_id(user_id)
        if user is None:
            raise InvalidAccountLinkError()
        return user, await self._issue_session(user_id)

    # -- internals ----------------------------------------------------------

    async def _deliver(self, recipient: str, email: ComposedEmail, link: str) -> None:
        if self._mailer is None:
            logger.info(
                "Email sending is off (SMTP_HOST unset); link not sent, open it "
                "yourself: %s",
                link,
            )
            return
        await self._mailer.send(recipient=recipient, email=email)

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
        return normalize_email(email)

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
