"""The email notification service: composition, transport, and delivery rules.

No socket is opened anywhere here. `compose` is pure, so the copy is asserted
directly; the transport is exercised through a fake `smtplib.SMTP` so auth,
STARTTLS, and the timeout can be checked without a relay.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from backend.config import EmailSettings
from backend.integrations.email import (
    ComposedEmail,
    EmailNotificationService,
    SmtplibSender,
    compose,
)
from backend.models.reservation import (
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.notification_service import WatchEvent


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DASHBOARD = "https://dibs.example.com"


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Bhima's Warung",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def _slot() -> AvailabilitySlot:
    return AvailabilitySlot(
        slot_id="slot_1",
        provider="mock",
        venue_name="Bhima's Warung",
        venue_type=VenueType.RESTAURANT,
        date="2026-09-05",
        start_time="19:00",
        end_time="21:00",
        party_size=4,
        max_party_size=4,
    )


def _watch(
    *, status: WatchStatus = WatchStatus.ACTIVE, booking=None, slots=None
) -> Watch:
    return Watch(
        watch_id="watch_1",
        status=status,
        query=_query(),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=2,
        max_attempts=10,
        next_check_at=NOW if status is WatchStatus.ACTIVE else None,
        found_slots=slots or [],
        booking=booking,
    )


def _booked_watch() -> Watch:
    return _watch(
        status=WatchStatus.BOOKED,
        slots=[_slot()],
        booking=BookingConfirmation(
            booking_id="booking_abc123",
            provider="mock",
            status=BookingStatus.MOCK_CONFIRMED,
            slot=_slot(),
            created_at=NOW,
        ),
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- composition (pure) ----------------------------------------------------


def test_a_found_message_names_the_venue_and_the_outcome() -> None:
    email = compose(_watch(), WatchEvent.AVAILABILITY_FOUND, DASHBOARD)

    assert "Bhima's Warung" in email.subject
    assert "2026-09-05" in email.subject
    assert "party of 4" in email.body
    assert "19:00" in email.body


def test_a_found_message_says_the_table_is_not_held() -> None:
    """A notify-only watch found a slot but did not take it; saying so is the
    difference between a useful alert and a misleading one."""

    email = compose(_watch(), WatchEvent.AVAILABILITY_FOUND, DASHBOARD)

    assert "not held" in email.body


def test_a_booked_message_carries_the_confirmation_id() -> None:
    """Requirement 1.3: actionable without opening the app."""

    email = compose(_booked_watch(), WatchEvent.BOOKED, DASHBOARD)

    assert "Booked" in email.subject
    assert "booking_abc123" in email.body


def test_a_booked_message_survives_a_missing_confirmation() -> None:
    email = compose(
        _watch(status=WatchStatus.ACTIVE), WatchEvent.BOOKED, DASHBOARD
    )

    assert "Booked" in email.subject  # no crash, just no confirmation line


def test_an_expired_message_explains_that_nothing_opened() -> None:
    email = compose(_watch(), WatchEvent.EXPIRED, DASHBOARD)

    assert "ended" in email.subject
    assert "Nothing opened up" in email.body


def test_every_message_links_back_to_the_dashboard() -> None:
    for event in WatchEvent:
        email = compose(_watch(), event, DASHBOARD)
        assert f"{DASHBOARD}/watches" in email.body


# --- transport -------------------------------------------------------------


class _FakeSMTP:
    """Captures what a real relay would have been asked to do."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int | float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[object] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: object) -> None:
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr("backend.integrations.email.smtplib.SMTP", _FakeSMTP)
    return _FakeSMTP


def _settings(**overrides) -> EmailSettings:
    base = {
        "host": "smtp.example.com",
        "port": 587,
        "sender": "dibs@example.com",
        "timeout_seconds": 10,
    }
    return EmailSettings(**{**base, **overrides})


def test_the_sender_delivers_a_well_formed_message(fake_smtp) -> None:
    sender = SmtplibSender(_settings())

    _run(
        sender.send(
            recipient="scout@example.com",
            email=ComposedEmail(subject="A table opened", body="Move quickly."),
        )
    )

    smtp = fake_smtp.instances[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.com", 587, 10)
    message = smtp.sent[0]
    assert message["To"] == "scout@example.com"
    assert message["From"] == "dibs@example.com"
    assert message["Subject"] == "A table opened"
    assert "Move quickly." in message.get_content()


def test_the_sender_negotiates_tls_and_authenticates_when_configured(
    fake_smtp,
) -> None:
    sender = SmtplibSender(
        _settings(username="dibs", password="s3cr3t", starttls=True)
    )

    _run(
        sender.send(
            recipient="scout@example.com",
            email=ComposedEmail(subject="s", body="b"),
        )
    )

    smtp = fake_smtp.instances[0]
    assert smtp.started_tls is True
    assert smtp.login_args == ("dibs", "s3cr3t")


def test_the_sender_skips_tls_and_auth_for_an_open_relay(fake_smtp) -> None:
    sender = SmtplibSender(_settings(starttls=False))

    _run(
        sender.send(
            recipient="scout@example.com",
            email=ComposedEmail(subject="s", body="b"),
        )
    )

    smtp = fake_smtp.instances[0]
    assert smtp.started_tls is False
    assert smtp.login_args is None


def test_the_sender_refuses_to_be_built_without_configuration() -> None:
    with pytest.raises(ValueError):
        SmtplibSender(EmailSettings())


# --- the service -----------------------------------------------------------


class _StubResolver:
    def __init__(self, address: str | None) -> None:
        self.address = address
        self.asked: list[str] = []

    async def email_for_watch(self, watch_id: str) -> str | None:
        self.asked.append(watch_id)
        return self.address


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, ComposedEmail]] = []

    async def send(self, *, recipient: str, email: ComposedEmail) -> None:
        self.sent.append((recipient, email))


class _FailingSender:
    async def send(self, *, recipient: str, email: ComposedEmail) -> None:
        raise RuntimeError("relay refused the message")


def _service(address: str | None, sender=None):
    return EmailNotificationService(
        resolver=_StubResolver(address),
        sender=sender or _RecordingSender(),
        dashboard_base_url=DASHBOARD,
    )


def test_an_owned_watch_is_emailed_once() -> None:
    sender = _RecordingSender()
    service = _service("scout@example.com", sender)

    _run(service.notify(_booked_watch(), WatchEvent.BOOKED))

    assert len(sender.sent) == 1
    recipient, email = sender.sent[0]
    assert recipient == "scout@example.com"
    assert "booking_abc123" in email.body


def test_an_anonymous_watch_sends_nothing_and_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 2.2: no account is an ordinary outcome, not a warning."""

    sender = _RecordingSender()
    service = _service(None, sender)

    with caplog.at_level(logging.WARNING):
        _run(service.notify(_watch(), WatchEvent.AVAILABILITY_FOUND))

    assert sender.sent == []
    assert caplog.records == []


def test_the_recipient_address_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 6.3."""

    service = _service("scout@example.com")

    with caplog.at_level(logging.DEBUG):
        _run(service.notify(_watch(), WatchEvent.AVAILABILITY_FOUND))

    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert "scout@example.com" not in rendered
    assert "watch_1" in rendered  # still correlatable


def test_a_transport_failure_propagates_for_watch_service_to_isolate() -> None:
    """Delivery is best-effort and never retried here: the raise is caught by
    `WatchService._notify`, which is what stops a poll retry emailing twice."""

    service = _service("scout@example.com", _FailingSender())

    with pytest.raises(RuntimeError, match="relay refused"):
        _run(service.notify(_watch(), WatchEvent.AVAILABILITY_FOUND))
