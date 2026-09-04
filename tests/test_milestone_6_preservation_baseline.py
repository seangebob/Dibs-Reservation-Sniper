"""Preservation baselines Milestone 6 (notification delivery) must not violate.

Characterization tests locking today's PRE-DELIVERY contract on the surfaces
Milestone 6 touches:

- Enduring: a terminal transition announces itself exactly once per event, and
  cancellation stays event-free (Milestone 3's rule). Nothing public exposes a
  recipient, and the default with no notifier injected is the logging one.
- Updated by Task 2: the two assertions that pinned the pre-fix behavior (a
  raising notifier escaping a committed transition, and the legacy paths
  announcing before they projected) are inverted here, which is exactly the
  visible, deliberate change Task 1 existed to set up.
- Updated by Task 7: the worker now composes both collaborators when PostgreSQL
  is configured; what remains pinned here is the standalone case, where it still
  composes neither. The configured case lives in test_worker_projection.py.
- Added by Task 8: one end-to-end privacy sentinel over a real terminal
  transition with delivery composed.

Deliberately overlaps other suites: the point of a baseline is to survive a
future refactor of the suites those assertions live in.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchPollOutcome, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.notification_service import (
    LoggingNotificationService,
    RecordingNotificationService,
    WatchEvent,
)
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue
from backend.workers.scheduler import PollSchedule


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
TARGET_DATE = "2026-09-05"

QUERY_JSON = {
    "venue_name": "Cote",
    "venue_type": "RESTAURANT",
    "market": "Kitchener-Waterloo-Cambridge, ON",
    "party_size": 4,
    "date": "2026-12-31",
    "preferred_time": "19:00",
    "time_window": None,
    "duration_minutes": None,
    "special_requests": [],
}


def query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date=TARGET_DATE,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


class _EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a watch keeps polling until exhausted."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


def _build(adapter=None, *, notifier=None, history=None, max_attempts: int = 10):
    service = WatchService(
        InMemoryWatchRepository(),
        adapter or _EmptyAdapter(),
        RecordingTaskQueue(),
        schedule=PollSchedule(interval_seconds=180, jitter_seconds=30),
        notifier=notifier,
        history=history,
        max_attempts=max_attempts,
        clock=lambda: NOW,
    )
    return service


def _legacy_watch() -> Watch:
    """A pre-Milestone-3 record with no runtime sidecar, so `poll_once` falls
    back to the legacy path."""

    return Watch(
        watch_id="watch_legacy",
        status=WatchStatus.ACTIVE,
        query=query(),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=NOW,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Enduring: one announcement per terminal event, and none for cancellation.
# ---------------------------------------------------------------------------


def test_the_default_notifier_is_the_logging_one() -> None:
    """No notifier injected must keep working, and keep logging (Req 5.1)."""

    assert isinstance(_build()._notifier, LoggingNotificationService)


def test_a_found_poll_announces_exactly_once() -> None:
    notifier = RecordingNotificationService()
    service = _build(MockBookingAdapter(), notifier=notifier)
    watch = _run(service.create(query()))
    notifier.events.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert notifier.events == [(watch.watch_id, WatchEvent.AVAILABILITY_FOUND)]


def test_an_exhausted_poll_announces_expiry_exactly_once() -> None:
    notifier = RecordingNotificationService()
    service = _build(notifier=notifier, max_attempts=1)
    watch = _run(service.create(query()))
    notifier.events.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.EXPIRED
    assert notifier.events == [(watch.watch_id, WatchEvent.EXPIRED)]


def test_creating_a_watch_announces_nothing() -> None:
    notifier = RecordingNotificationService()
    service = _build(notifier=notifier)

    _run(service.create(query()))

    assert notifier.events == []


def test_cancelling_a_watch_stays_event_free() -> None:
    """Milestone 3's rule: a cancellation is the owner's own action, so it is
    never announced back to them."""

    notifier = RecordingNotificationService()
    service = _build(notifier=notifier)
    watch = _run(service.create(query()))
    notifier.events.clear()

    cancelled = _run(service.cancel(watch.watch_id))

    assert cancelled is not None and cancelled.status is WatchStatus.CANCELLED
    assert notifier.events == []


# ---------------------------------------------------------------------------
# Updated by Task 2: notification is now best-effort, bounded, and second on
# every path. The two assertions that pinned the old behavior are inverted
# below, which is exactly the visible, deliberate change Task 1 set up.
# ---------------------------------------------------------------------------


class _SequenceNotifier:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        self._log.append("notify")


class _SequenceHistory:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def record(self, watch, owner_client_id=None, user_id=None) -> None:
        self._log.append("record")


def test_the_windowed_path_already_records_before_it_notifies() -> None:
    """The primary (fenced, windowed) path is already in the right order:
    `_commit_window` writes history on COMMITTED, and only then does the caller
    notify. Milestone 6 preserves this; it is the legacy paths that differ."""

    order: list[str] = []
    service = _build(
        MockBookingAdapter(),
        notifier=_SequenceNotifier(order),
        history=_SequenceHistory(order),
    )
    watch = _run(service.create(query()))
    order.clear()

    _run(service.poll_once(watch.watch_id))

    assert order == ["record", "notify"]


def test_the_legacy_paths_now_record_before_they_notify() -> None:
    """Task 2 reversed these four sites so the durable record wins.

    A watch with no runtime sidecar (a pre-Milestone-3 record) falls back to the
    legacy poll; it used to announce before it projected, so a delivery failure
    cost the dashboard its terminal state.
    """

    order: list[str] = []
    repository = InMemoryWatchRepository()
    _run(repository.save(_legacy_watch()))
    service = WatchService(
        repository,
        MockBookingAdapter(),
        RecordingTaskQueue(),
        schedule=PollSchedule(interval_seconds=180, jitter_seconds=30),
        notifier=_SequenceNotifier(order),
        history=_SequenceHistory(order),
        clock=lambda: NOW,
    )

    result = _run(service.poll_once("watch_legacy"))

    assert result.outcome is WatchPollOutcome.FOUND
    assert order == ["record", "notify"]


def test_the_windowed_path_gates_delivery_to_at_most_once() -> None:
    """Milestone 3 already issues a terminal event id at most once per
    transition and gates the notification on it. Milestone 6 relies on this
    rather than adding a delivery state machine, so it is pinned here."""

    notifier = RecordingNotificationService()
    service = _build(MockBookingAdapter(), notifier=notifier)
    watch = _run(service.create(query()))
    notifier.events.clear()

    first = _run(service.poll_once(watch.watch_id))
    second = _run(service.poll_once(watch.watch_id))

    assert first.outcome is WatchPollOutcome.FOUND
    # The watch is already terminal, so the second delivery announces nothing.
    assert second.outcome is WatchPollOutcome.ALREADY_FINISHED
    assert notifier.events == [(watch.watch_id, WatchEvent.AVAILABILITY_FOUND)]


class _RaisingNotifier:
    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        raise RuntimeError("mail server is down")


class _HangingNotifier:
    """Never returns, the way a wedged relay holding a socket open would."""

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        await asyncio.sleep(3600)


def test_a_raising_notifier_no_longer_breaks_the_poll() -> None:
    """The defect Task 2 fixed. This assertion was inverted from Task 1's
    baseline, where the RuntimeError escaped the committed transition."""

    order: list[str] = []
    service = _build(
        MockBookingAdapter(),
        notifier=_RaisingNotifier(),
        history=_SequenceHistory(order),
    )
    watch = _run(service.create(query()))
    order.clear()

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert result.watch is not None and result.watch.status is WatchStatus.FOUND
    # The history write still happened: the failure cost an email, nothing else.
    assert order == ["record"]


def test_a_hanging_notifier_cannot_outlive_its_timeout() -> None:
    """A poll holds a Milestone 3 window lease while it announces, so an
    unbounded wait on a wedged relay would be a coordination bug (Req 3.2)."""

    service = _build(MockBookingAdapter(), notifier=_HangingNotifier())
    # Well under the lease; the point is that it returns at all.
    service._notify_timeout_seconds = 0.05
    watch = _run(service.create(query()))

    result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND


def test_a_failing_notification_is_identical_to_a_succeeding_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 3.1 in its strongest form: the caller cannot tell."""

    good = _build(MockBookingAdapter(), notifier=RecordingNotificationService())
    good_watch = _run(good.create(query()))
    good_result = _run(good.poll_once(good_watch.watch_id))

    bad = _build(MockBookingAdapter(), notifier=_RaisingNotifier())
    bad_watch = _run(bad.create(query()))
    with caplog.at_level("WARNING"):
        bad_result = _run(bad.poll_once(bad_watch.watch_id))

    assert good_result.outcome is bad_result.outcome
    assert good_result.watch is not None and bad_result.watch is not None
    assert good_result.watch.status is bad_result.watch.status
    assert good_result.watch.found_slots == bad_result.watch.found_slots
    # The failure is not silent, it is just not the caller's problem.
    assert any("watch notification failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Updated by Task 7: with no PostgreSQL the worker still composes neither
# collaborator, which is the enduring standalone guarantee (Requirement 4.3).
# The configured case is covered in test_worker_projection.py.
# ---------------------------------------------------------------------------


def test_the_worker_service_stays_bare_without_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `POSTGRES_URL` must behave exactly as every Milestone 1-5 worker did:
    no projection, no email, and above all no startup failure."""

    pytest.importorskip("celery", reason="requires the worker extra")
    pytest.importorskip("kombu", reason="requires the worker extra")
    from backend.workers.tasks import monitor_watch as task_module

    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(
        task_module, "_settings", lambda: Settings(openai_api_key="test-key")
    )
    monkeypatch.setattr(task_module, "_redis_client", lambda: object())
    task_module.build_watch_service.cache_clear()
    task_module._postgres_pool.cache_clear()
    try:
        service = task_module.build_watch_service()
        assert service._history is None
        assert isinstance(service._notifier, LoggingNotificationService)
    finally:
        task_module.build_watch_service.cache_clear()
        task_module._postgres_pool.cache_clear()


# ---------------------------------------------------------------------------
# Enduring (Req 6.5): nothing public carries a recipient.
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[get_watch_service] = lambda: WatchService(
        InMemoryWatchRepository(), _EmptyAdapter(), RecordingTaskQueue()
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_no_public_watch_body_carries_a_recipient(client: TestClient) -> None:
    created = client.post("/api/watches", json=QUERY_JSON)
    assert created.status_code == 201

    body = created.json()
    for leaked in ("email", "recipient", "notify_to", "user_id", "owner_client_id"):
        assert leaked not in body


# ---------------------------------------------------------------------------
# Task 8: one end-to-end privacy sentinel over a real terminal transition,
# with email delivery actually composed (Requirements 6.1, 6.2, 6.3).
# ---------------------------------------------------------------------------


def test_a_full_terminal_transition_leaks_nothing_into_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Everything the system logs while finding, projecting, and emailing a
    watch -- scanned for the reservation, the recipient, and the credential."""

    from backend.config import EmailSettings
    from backend.integrations.email import ComposedEmail, EmailNotificationService

    sent: list[ComposedEmail] = []

    class _Resolver:
        async def email_for_watch(self, watch_id: str) -> str | None:
            return "scout@example.com"

    class _Sender:
        async def send(self, *, recipient: str, email: ComposedEmail) -> None:
            sent.append(email)

    # Proves the sentinel would notice a password if one were ever logged.
    settings = EmailSettings(
        host="smtp.example.com",
        sender="dibs@example.com",
        username="dibs",
        password="s3cr3t-sentinel",
    )
    assert settings.password == "s3cr3t-sentinel"

    service = _build(
        MockBookingAdapter(),
        notifier=EmailNotificationService(
            resolver=_Resolver(),
            sender=_Sender(),
            dashboard_base_url="https://dibs.example.com",
        ),
        history=_SequenceHistory([]),
    )

    with caplog.at_level(logging.DEBUG):
        watch = _run(service.create(query()))
        result = _run(service.poll_once(watch.watch_id))

    assert result.outcome is WatchPollOutcome.FOUND
    assert len(sent) == 1  # the email really was composed and delivered

    rendered = " ".join(record.getMessage() for record in caplog.records)
    for private in (
        "Cote",  # venue
        TARGET_DATE,  # reservation date
        "party",  # party size
        "scout@example.com",  # recipient
        "s3cr3t-sentinel",  # SMTP credential
    ):
        assert private not in rendered, f"{private!r} leaked into the logs"

    # The email itself of course carries the reservation -- that is its job.
    assert "Cote" in sent[0].subject
