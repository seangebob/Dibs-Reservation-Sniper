"""Outbound email delivery for terminal watch events (Milestone 6).

The thing Dibs has never been able to do: tell someone. `LoggingNotificationService`
has been the entire outbound story since Milestone 3, which meant a watch could
find a table at 2am and the only record was a log line nobody reads.

Three pieces, each replaceable on its own:

- `compose(...)` is pure -- a `Watch` and an event in, a subject and body out, so
  the wording is testable without a network or a settings object.
- `SmtpSender` is the transport. `SmtplibSender` uses the standard library, so
  the provider (Gmail, SendGrid, Mailgun, Postmark) stays a configuration value
  rather than a dependency. `smtplib` blocks, so it runs on a worker thread.
- `EmailNotificationService` satisfies the existing `NotificationService`
  protocol, so `WatchService` needs no knowledge of any of this.

Delivery is best-effort and never retried: a failure is raised to
`WatchService._notify`, which logs and moves on. That is deliberate -- see the
milestone design -- and it is what keeps a poll retry from emailing someone
twice. The durable record remains the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from backend.config import EmailSettings
from backend.models.watch import Watch
from backend.services.notification_service import WatchEvent
from backend.services.recipients import RecipientResolver


__all__ = [
    "ComposedEmail",
    "EmailNotificationService",
    "SmtpSender",
    "SmtplibSender",
    "compose",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ComposedEmail:
    subject: str
    body: str


def compose(watch: Watch, event: WatchEvent, dashboard_base_url: str) -> ComposedEmail:
    """Render one plain-text message for a terminal watch event.

    Pure and side-effect free, so the copy can be asserted directly. The subject
    names the venue and the outcome (Requirement 1.2) because that is all a
    phone's notification shade will show.
    """

    query = watch.query
    venue = query.venue_name
    when = f"{query.date} at {query.preferred_time}"
    party = f"party of {query.party_size}"
    dashboard = f"{dashboard_base_url}/watches"

    if event is WatchEvent.BOOKED:
        subject = f"Booked: {venue} on {query.date}"
        opening = (
            f"Dibs booked your table at {venue} for {when} ({party}).\n\n"
            "It grabbed the slot the moment it opened, so you should not need "
            "to do anything else."
        )
        if watch.booking is not None:
            opening += f"\n\nConfirmation: {watch.booking.booking_id}"
    elif event is WatchEvent.AVAILABILITY_FOUND:
        subject = f"A table opened at {venue} — {query.date}"
        opening = (
            f"Dibs found availability at {venue} for {when} ({party}).\n\n"
            "This watch was set to notify rather than book, so the table is "
            "not held. Move quickly."
        )
    else:  # EXPIRED
        subject = f"Your {venue} watch has ended"
        opening = (
            f"Dibs stopped watching {venue} for {when} ({party}).\n\n"
            "Nothing opened up before the watch reached its limit. You can "
            "start a new one any time."
        )

    body = f"{opening}\n\nSee your watches: {dashboard}\n\n-- Dibs\n"
    return ComposedEmail(subject=subject, body=body)


class SmtpSender(Protocol):
    """The transport. Injectable so tests never open a socket."""

    async def send(self, *, recipient: str, email: ComposedEmail) -> None: ...


class SmtplibSender:
    """Standard-library SMTP delivery on a worker thread.

    `smtplib` is blocking, and this runs inside the async poll path, so the call
    goes through `asyncio.to_thread`. The socket also carries its own timeout:
    `WatchService._notify` bounds the await, but only a transport-level timeout
    stops a wedged relay from holding a thread from the default executor pool
    long after that wait has been abandoned.
    """

    def __init__(self, settings: EmailSettings) -> None:
        if not settings.enabled or settings.sender is None:
            raise ValueError("SmtplibSender requires configured email settings")
        self._settings = settings

    async def send(self, *, recipient: str, email: ComposedEmail) -> None:
        await asyncio.to_thread(self._send_blocking, recipient, email)

    def _send_blocking(self, recipient: str, email: ComposedEmail) -> None:
        settings = self._settings
        message = EmailMessage()
        message["Subject"] = email.subject
        message["From"] = settings.sender or ""
        message["To"] = recipient
        message.set_content(email.body)

        with smtplib.SMTP(
            settings.host or "", settings.port, timeout=settings.timeout_seconds
        ) as smtp:
            if settings.starttls:
                smtp.starttls()
            if settings.username is not None and settings.password is not None:
                smtp.login(settings.username, settings.password)
            smtp.send_message(message)


class EmailNotificationService:
    """Announces a terminal watch event to the owning account by email."""

    def __init__(
        self,
        *,
        resolver: RecipientResolver,
        sender: SmtpSender,
        dashboard_base_url: str,
    ) -> None:
        self._resolver = resolver
        self._sender = sender
        self._dashboard_base_url = dashboard_base_url

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        """Send one message, or nothing when there is nobody to tell.

        An anonymous watch resolving to no recipient is an ordinary outcome and
        is silent -- not a warning (Requirement 2.2). The recipient address is
        never logged (Requirement 6.3).
        """

        recipient = await self._resolver.email_for_watch(watch.watch_id)
        if recipient is None:
            return
        await self._sender.send(
            recipient=recipient,
            email=compose(watch, event, self._dashboard_base_url),
        )
        logger.info(
            "watch=%s event=%s notification emailed",
            watch.watch_id,
            event.value,
        )
