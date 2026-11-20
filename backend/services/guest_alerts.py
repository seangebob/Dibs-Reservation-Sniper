"""Email alerts for guests: no account, just a verified address (roadmap 3b).

A guest is the anonymous `X-Dibs-Client-Id` that owns their watches. They give
an address; Dibs emails a single-use link that expires in a day; opening it
makes that address the one that hears about that browser's watches. The link
can be opened anywhere (a phone's mail app, say), because the request already
recorded which browser it belongs to.

Only a sha256 of each link's token is stored. Each browser, and each address,
may be sent a few links an hour, so Dibs can't be used to flood an inbox.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from backend.integrations.email import ComposedEmail, SmtpSender
from backend.services.auth_service import hash_token, normalize_email
from backend.services.throttle import RateLimitedError, SlidingWindowThrottle


__all__ = [
    "GuestAlertService",
    "InvalidVerificationLinkError",
    "TooManyVerificationEmailsError",
    "VerificationEmailFailedError",
    "compose_verification",
]

logger = logging.getLogger(__name__)

VERIFICATION_TTL = timedelta(hours=24)
#: How many links one browser, and one address, may be sent per hour.
LINKS_PER_HOUR = 3
_HOUR_SECONDS = 3_600


class InvalidVerificationLinkError(Exception):
    """The link is unknown, already used, or expired (-> 400)."""

    def __init__(self) -> None:
        super().__init__(
            "This link is invalid, already used, or expired. Ask for a new one."
        )


class TooManyVerificationEmailsError(RateLimitedError):
    """Too many links requested for this browser or address (-> 429)."""

    def __init__(self) -> None:
        super().__init__("Too many confirmation emails. Try again in an hour.")


class VerificationEmailFailedError(Exception):
    """The mail relay refused or failed to take the message (-> 502)."""

    def __init__(self) -> None:
        super().__init__("Couldn't send the confirmation email. Try again shortly.")


class _Store(Protocol):
    async def add_verification(
        self, token_hash: str, client_id: str, email: str, expires_at: datetime
    ) -> None: ...

    async def consume_verification(
        self, token_hash: str, now: datetime
    ) -> tuple[str, str] | None: ...

    async def set_email(self, client_id: str, email: str, verified_at: datetime) -> None: ...

    async def email_for_client(self, client_id: str) -> str | None: ...

    async def remove_email(self, client_id: str) -> None: ...


def compose_verification(link: str) -> ComposedEmail:
    """The confirmation message. Plain text, like the alerts."""

    return ComposedEmail(
        subject="Confirm your email for Dibs alerts",
        body=(
            "Open this link to get Dibs alerts at this address when a table "
            f"opens:\n\n{link}\n\n"
            "It works once and expires in 24 hours. If you didn't ask for "
            "this, ignore this email and nothing will be sent.\n\n-- Dibs\n"
        ),
    )


class GuestAlertService:
    """Sends verification links and keeps each guest's verified address."""

    def __init__(
        self,
        *,
        store: _Store,
        sender: SmtpSender | None,
        dashboard_base_url: str,
        clock: Callable[[], datetime] | None = None,
        throttle_clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._base_url = dashboard_base_url.rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client_throttle = SlidingWindowThrottle(
            max_events=LINKS_PER_HOUR,
            window_seconds=_HOUR_SECONDS,
            on_limit=TooManyVerificationEmailsError,
            clock=throttle_clock,
        )
        self._email_throttle = SlidingWindowThrottle(
            max_events=LINKS_PER_HOUR,
            window_seconds=_HOUR_SECONDS,
            on_limit=TooManyVerificationEmailsError,
            clock=throttle_clock,
        )

    async def request_verification(self, client_id: str, email: str) -> None:
        """Email a single-use link that makes `email` this browser's alert address."""

        address = normalize_email(email)
        self._client_throttle.check(client_id)
        self._email_throttle.check(address)

        raw = secrets.token_urlsafe(32)
        await self._store.add_verification(
            hash_token(raw), client_id, address, self._clock() + VERIFICATION_TTL
        )
        self._client_throttle.record(client_id)
        self._email_throttle.record(address)

        link = f"{self._base_url}/verify-email?token={raw}"
        if self._sender is None:
            # No SMTP relay configured (local development): alerts are log-only
            # too, so print the link where the developer can click it.
            logger.info(
                "Email sending is off (SMTP_HOST unset); confirmation link not "
                "sent, open it yourself: %s",
                link,
            )
            return
        try:
            await self._sender.send(recipient=address, email=compose_verification(link))
        except Exception as exc:  # noqa: BLE001 -- any relay failure reads the same
            logger.warning("Confirmation email failed to send: %s", type(exc).__name__)
            raise VerificationEmailFailedError() from exc

    async def verify(self, raw_token: str) -> str:
        """Use a link: its address becomes the requesting browser's alert address."""

        result = await self._store.consume_verification(
            hash_token(raw_token.strip()), self._clock()
        )
        if result is None:
            raise InvalidVerificationLinkError()
        client_id, address = result
        await self._store.set_email(client_id, address, self._clock())
        return address

    async def email_for_client(self, client_id: str) -> str | None:
        return await self._store.email_for_client(client_id)

    async def remove(self, client_id: str) -> None:
        """Stop alerts to this browser's address."""

        await self._store.remove_email(client_id)
