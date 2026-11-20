"""Email alerts for guests: a verified address, no account (roadmap 3b)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.integrations.email import ComposedEmail
from backend.main import create_app
from backend.models.account import User
from backend.services.auth_service import InvalidEmailError, hash_token
from backend.services.guest_alerts import (
    GuestAlertService,
    InvalidVerificationLinkError,
    TooManyVerificationEmailsError,
    VerificationEmailFailedError,
)
from backend.services.recipients import AccountRecipientResolver


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CLIENT = "client_abc"


class FakeStore:
    """In-memory stand-in with the repository's semantics."""

    def __init__(self) -> None:
        self.verifications: dict[str, dict[str, object]] = {}
        self.emails: dict[str, str] = {}

    async def add_verification(
        self, token_hash: str, client_id: str, email: str, expires_at: datetime
    ) -> None:
        self.verifications[token_hash] = {
            "client_id": client_id,
            "email": email,
            "expires_at": expires_at,
            "used_at": None,
        }

    async def consume_verification(
        self, token_hash: str, now: datetime
    ) -> tuple[str, str] | None:
        row = self.verifications.get(token_hash)
        if row is None or row["used_at"] is not None or row["expires_at"] <= now:  # type: ignore[operator]
            return None
        row["used_at"] = now
        return str(row["client_id"]), str(row["email"])

    async def set_email(self, client_id: str, email: str, verified_at: datetime) -> None:
        self.emails[client_id] = email

    async def email_for_client(self, client_id: str) -> str | None:
        return self.emails.get(client_id)

    async def remove_email(self, client_id: str) -> None:
        self.emails.pop(client_id, None)


class FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, ComposedEmail]] = []
        self.fail = fail

    async def send(self, *, recipient: str, email: ComposedEmail) -> None:
        if self.fail:
            raise OSError("relay down")
        self.sent.append((recipient, email))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _service(
    store: FakeStore | None = None, sender: FakeSender | None = None, clock: Clock | None = None
) -> tuple[GuestAlertService, FakeStore, FakeSender | None, Clock]:
    store = store or FakeStore()
    clock = clock or Clock()
    service = GuestAlertService(
        store=store,
        sender=sender,
        dashboard_base_url="https://dibs.example.com/",
        clock=clock,
        throttle_clock=lambda: 0.0,
    )
    return service, store, sender, clock


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _token_from(email: ComposedEmail) -> str:
    marker = "/verify-email?token="
    start = email.body.index(marker) + len(marker)
    return email.body[start:].split()[0]


# --- requesting a link -------------------------------------------------------


def test_a_request_emails_a_single_use_link_and_stores_only_its_hash() -> None:
    service, store, sender, _ = _service(sender=FakeSender())

    _run(service.request_verification(CLIENT, "  Diner@Example.COM "))

    assert sender is not None
    [(recipient, email)] = sender.sent
    assert recipient == "diner@example.com"
    assert "https://dibs.example.com/verify-email?token=" in email.body
    assert "expires in 24 hours" in email.body
    token = _token_from(email)
    [(stored_hash, row)] = store.verifications.items()
    assert stored_hash == hash_token(token)
    assert token not in str(row)
    assert row["expires_at"] == NOW + timedelta(hours=24)
    # Nothing is sent to an unconfirmed address but the confirmation itself.
    assert store.emails == {}


def test_a_malformed_address_is_rejected() -> None:
    service, _, sender, _ = _service(sender=FakeSender())

    with pytest.raises(InvalidEmailError):
        _run(service.request_verification(CLIENT, "not-an-email"))

    assert sender is not None and sender.sent == []


def test_one_browser_gets_three_links_an_hour() -> None:
    service, _, _, _ = _service(sender=FakeSender())
    for n in range(3):
        _run(service.request_verification(CLIENT, f"diner{n}@example.com"))

    with pytest.raises(TooManyVerificationEmailsError):
        _run(service.request_verification(CLIENT, "diner9@example.com"))


def test_one_address_gets_three_links_an_hour_from_any_browser() -> None:
    service, _, _, _ = _service(sender=FakeSender())
    for n in range(3):
        _run(service.request_verification(f"client_{n}", "target@example.com"))

    with pytest.raises(TooManyVerificationEmailsError):
        _run(service.request_verification("client_9", "Target@Example.com"))


def test_without_a_mail_relay_the_link_is_logged_for_local_use(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, store, _, _ = _service(sender=None)

    with caplog.at_level(logging.INFO, logger="backend.services.guest_alerts"):
        _run(service.request_verification(CLIENT, "diner@example.com"))

    assert "https://dibs.example.com/verify-email?token=" in caplog.text
    assert len(store.verifications) == 1


def test_a_relay_failure_is_reported_without_the_address(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _, _ = _service(sender=FakeSender(fail=True))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(VerificationEmailFailedError):
            _run(service.request_verification(CLIENT, "diner@example.com"))

    assert "diner@example.com" not in caplog.text


# --- opening the link ------------------------------------------------------


def test_opening_the_link_makes_the_address_this_browsers_alert_address() -> None:
    service, store, sender, _ = _service(sender=FakeSender())
    _run(service.request_verification(CLIENT, "diner@example.com"))
    assert sender is not None
    token = _token_from(sender.sent[0][1])

    assert _run(service.verify(token)) == "diner@example.com"
    assert _run(service.email_for_client(CLIENT)) == "diner@example.com"


def test_a_link_works_once() -> None:
    service, _, sender, _ = _service(sender=FakeSender())
    _run(service.request_verification(CLIENT, "diner@example.com"))
    assert sender is not None
    token = _token_from(sender.sent[0][1])
    _run(service.verify(token))

    with pytest.raises(InvalidVerificationLinkError):
        _run(service.verify(token))


def test_an_expired_link_does_nothing() -> None:
    service, store, sender, clock = _service(sender=FakeSender())
    _run(service.request_verification(CLIENT, "diner@example.com"))
    assert sender is not None
    token = _token_from(sender.sent[0][1])
    clock.now = NOW + timedelta(hours=24, seconds=1)

    with pytest.raises(InvalidVerificationLinkError):
        _run(service.verify(token))
    assert store.emails == {}


def test_an_unknown_link_does_nothing() -> None:
    service, _, _, _ = _service(sender=FakeSender())

    with pytest.raises(InvalidVerificationLinkError):
        _run(service.verify("made-up-token"))


def test_a_newer_address_replaces_the_old_one_and_can_be_removed() -> None:
    service, _, sender, _ = _service(sender=FakeSender())
    assert sender is not None
    for address in ("old@example.com", "new@example.com"):
        _run(service.request_verification(CLIENT, address))
        _run(service.verify(_token_from(sender.sent[-1][1])))

    assert _run(service.email_for_client(CLIENT)) == "new@example.com"
    _run(service.remove(CLIENT))
    assert _run(service.email_for_client(CLIENT)) is None


# --- who gets a watch's alert ----------------------------------------------


class FakeHistory:
    def __init__(self, *, account: UUID | None = None, guest: str | None = None) -> None:
        self.account = account
        self.guest = guest

    async def get_account_owner(self, watch_id: str) -> UUID | None:
        return self.account

    async def get_guest_owner(self, watch_id: str) -> str | None:
        return self.guest


class FakeAccounts:
    def __init__(self, user: User | None) -> None:
        self.user = user

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.user


def test_a_guest_watch_alerts_the_guests_verified_address() -> None:
    store = FakeStore()
    store.emails[CLIENT] = "diner@example.com"
    history = FakeHistory(guest=CLIENT)
    resolver = AccountRecipientResolver(history, FakeAccounts(None), guests=(history, store))

    assert _run(resolver.email_for_watch("watch_1")) == "diner@example.com"


def test_a_guest_without_a_verified_address_gets_nothing() -> None:
    history = FakeHistory(guest=CLIENT)
    resolver = AccountRecipientResolver(
        history, FakeAccounts(None), guests=(history, FakeStore())
    )

    assert _run(resolver.email_for_watch("watch_1")) is None


def test_an_account_watch_still_goes_to_the_account() -> None:
    user = User(id=uuid4(), email="owner@example.com", created_at=NOW)
    store = FakeStore()
    store.emails[CLIENT] = "diner@example.com"
    history = FakeHistory(account=user.id, guest=CLIENT)
    resolver = AccountRecipientResolver(history, FakeAccounts(user), guests=(history, store))

    assert _run(resolver.email_for_watch("watch_1")) == "owner@example.com"


# --- the API -----------------------------------------------------------------


@pytest.fixture
def client_and_sender() -> tuple[TestClient, FakeSender]:
    app = create_app()
    sender = FakeSender()
    with TestClient(app) as client:
        app.state.guest_alerts = GuestAlertService(
            store=FakeStore(), sender=sender, dashboard_base_url="https://dibs.example.com"
        )
        yield client, sender  # type: ignore[misc]


def test_the_api_runs_the_whole_flow(client_and_sender: tuple[TestClient, FakeSender]) -> None:
    client, sender = client_and_sender
    headers = {"X-Dibs-Client-Id": CLIENT}

    assert client.get("/api/alerts/email", headers=headers).json() == {"email": None}
    sent = client.post(
        "/api/alerts/email", headers=headers, json={"email": "diner@example.com"}
    )
    assert sent.status_code == 202
    # Not active until the link is opened.
    assert client.get("/api/alerts/email", headers=headers).json() == {"email": None}

    token = _token_from(sender.sent[0][1])
    verified = client.post("/api/alerts/email/verify", json={"token": token})
    assert verified.json() == {"email": "diner@example.com"}
    assert client.get("/api/alerts/email", headers=headers).json() == {
        "email": "diner@example.com"
    }

    again = client.post("/api/alerts/email/verify", json={"token": token})
    assert again.status_code == 400
    assert "already used" in again.json()["detail"]

    assert client.delete("/api/alerts/email", headers=headers).status_code == 204
    assert client.get("/api/alerts/email", headers=headers).json() == {"email": None}


def test_the_api_needs_a_browser_id(client_and_sender: tuple[TestClient, FakeSender]) -> None:
    client, _ = client_and_sender

    response = client.post("/api/alerts/email", json={"email": "diner@example.com"})

    assert response.status_code == 400
    assert "Reload the page" in response.json()["detail"]


def test_the_api_rejects_a_malformed_address(
    client_and_sender: tuple[TestClient, FakeSender],
) -> None:
    client, _ = client_and_sender

    response = client.post(
        "/api/alerts/email", headers={"X-Dibs-Client-Id": CLIENT}, json={"email": "nope"}
    )

    assert response.status_code == 422


def test_the_api_rate_limits_links(client_and_sender: tuple[TestClient, FakeSender]) -> None:
    client, _ = client_and_sender
    headers = {"X-Dibs-Client-Id": CLIENT}
    codes = [
        client.post(
            "/api/alerts/email", headers=headers, json={"email": f"d{n}@example.com"}
        ).status_code
        for n in range(4)
    ]

    assert codes == [202, 202, 202, 429]


def test_without_postgres_email_alerts_are_unavailable() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/alerts/email", headers={"X-Dibs-Client-Id": CLIENT})

    assert response.status_code == 503
    assert "PostgreSQL" in response.json()["detail"]
