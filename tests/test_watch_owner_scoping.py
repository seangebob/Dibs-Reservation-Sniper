"""Task 5: the anonymous `X-Dibs-Client-Id` header, threaded end to end.

Covers: the validation helper in isolation, `POST /api/watches` threading the
header into `WatchService.create`, `POST /api/parse-and-book` threading it
through `PromptRouter`/`create_from_intent`, the new owner-scoped
`GET /api/watches/mine` route, and that every existing (no-header) caller
keeps its exact prior body/status shape -- `owner_client_id` is additive and
never appears on the public `Watch` model (Requirement 6.2).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from backend.api.client_identity import extract_client_id
from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

QUERY = {
    "venue_name": "Cote",
    "venue_type": "RESTAURANT",
    "market": "Kitchener-Waterloo-Cambridge, ON",
    "party_size": 4,
    "date": "2026-09-05",
    "preferred_time": "19:00",
    "time_window": None,
    "duration_minutes": None,
    "special_requests": [],
}


class EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a created watch stays ACTIVE."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


class RecordingHistory:
    """Collects `record()` calls; the `Watch` gets a durable owner from this."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str | None]] = []
        self._owners: dict[str, str] = {}

    async def record(self, watch: Watch, owner_client_id: str | None = None) -> None:
        self.records.append((watch.watch_id, owner_client_id))
        if owner_client_id is not None:
            self._owners[watch.watch_id] = owner_client_id

    async def list_for_owner(self, owner_client_id: str, *, limit: int = 100):
        return []  # not exercised directly; the HTTP-level tests use FakeHistory


class FakeHistory:
    """A minimal, in-memory stand-in for `WatchHistoryRepository`."""

    def __init__(self) -> None:
        self._by_owner: dict[str, list[Watch]] = {}

    def seed(self, owner_client_id: str, watch: Watch) -> None:
        self._by_owner.setdefault(owner_client_id, []).append(watch)

    async def record(self, watch: Watch, owner_client_id: str | None = None) -> None:
        if owner_client_id is not None:
            self.seed(owner_client_id, watch)

    async def list_for_owner(self, owner_client_id: str, *, limit: int = 100):
        return list(self._by_owner.get(owner_client_id, []))[:limit]


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def _watch(watch_id: str = "watch_1") -> Watch:
    return Watch(
        watch_id=watch_id,
        status=WatchStatus.ACTIVE,
        query=_query(),
        auto_book=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=NOW,
    )


# ---------------------------------------------------------------------------
# extract_client_id: the validation helper in isolation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["visitor-1", "abc123", "a" * 200, "UUID-LIKE_token-9f2a"],
)
def test_a_well_formed_token_is_accepted_unchanged(raw: str) -> None:
    assert extract_client_id(raw) == raw


def test_none_is_accepted_as_anonymous() -> None:
    assert extract_client_id(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "a" * 201,
        "has a space",
        "has/a/slash",
        "has\nnewline",
        "<script>alert(1)</script>",
    ],
)
def test_a_malformed_token_degrades_to_none_rather_than_raising(raw: str) -> None:
    assert extract_client_id(raw) is None


def test_surrounding_whitespace_is_stripped() -> None:
    assert extract_client_id("  visitor-1  ") == "visitor-1"


# ---------------------------------------------------------------------------
# POST /api/watches: the header threads through to WatchService.create.
# ---------------------------------------------------------------------------


@pytest.fixture
def queue() -> RecordingTaskQueue:
    return RecordingTaskQueue()


@pytest.fixture
def history() -> RecordingHistory:
    return RecordingHistory()


@pytest.fixture
def client(queue: RecordingTaskQueue, history: RecordingHistory):
    app = create_app()
    service = WatchService(
        InMemoryWatchRepository(), EmptyAdapter(), queue, history=history
    )
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_creating_a_watch_with_a_client_id_records_it_as_owner(
    client: TestClient, history: RecordingHistory
) -> None:
    response = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-1"}
    )

    assert response.status_code == 201
    watch_id = response.json()["watch_id"]
    assert history.records == [(watch_id, "visitor-1")]


def test_creating_a_watch_without_a_client_id_records_no_owner(
    client: TestClient, history: RecordingHistory
) -> None:
    response = client.post("/api/watches", json=QUERY)

    assert response.status_code == 201
    watch_id = response.json()["watch_id"]
    assert history.records == [(watch_id, None)]


def test_a_malformed_client_id_header_creates_an_unowned_watch(
    client: TestClient, history: RecordingHistory
) -> None:
    response = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "has a space"}
    )

    assert response.status_code == 201
    watch_id = response.json()["watch_id"]
    assert history.records == [(watch_id, None)]


def test_owner_client_id_never_appears_in_the_public_watch_body(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-1"}
    )

    body = response.json()
    for forbidden in ("owner_client_id", "owner", "client_id"):
        assert forbidden not in body


# ---------------------------------------------------------------------------
# GET /api/watches/mine: owner-scoped listing over the history projection.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_history() -> FakeHistory:
    return FakeHistory()


@pytest.fixture
def scoped_client(fake_history: FakeHistory):
    app = create_app()
    app.state.watch_history = fake_history
    with TestClient(app) as test_client:
        yield test_client


def test_mine_returns_only_the_calling_clients_watches(
    scoped_client: TestClient, fake_history: FakeHistory
) -> None:
    fake_history.seed("visitor-1", _watch("watch_mine"))
    fake_history.seed("visitor-2", _watch("watch_theirs"))

    response = scoped_client.get(
        "/api/watches/mine", headers={"X-Dibs-Client-Id": "visitor-1"}
    )

    assert response.status_code == 200
    assert [w["watch_id"] for w in response.json()] == ["watch_mine"]


def test_mine_with_no_client_id_returns_an_empty_list_not_an_error(
    scoped_client: TestClient, fake_history: FakeHistory
) -> None:
    fake_history.seed("visitor-1", _watch("watch_mine"))

    response = scoped_client.get("/api/watches/mine")

    assert response.status_code == 200
    assert response.json() == []


def test_mine_with_no_history_configured_returns_an_empty_list() -> None:
    app = create_app()
    # app.state.watch_history stays the create_app() default (None): no
    # POSTGRES_URL configured, exactly like a real standalone deployment.
    with TestClient(app) as test_client:
        response = test_client.get(
            "/api/watches/mine", headers={"X-Dibs-Client-Id": "visitor-1"}
        )

    assert response.status_code == 200
    assert response.json() == []


def test_mine_is_never_shadowed_by_the_watch_id_path_parameter(
    scoped_client: TestClient,
) -> None:
    """`/mine` must route to the dedicated handler, not `read_watch(watch_id="mine")`."""

    response = scoped_client.get(
        "/api/watches/mine", headers={"X-Dibs-Client-Id": "visitor-1"}
    )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# POST /api/parse-and-book: the header reaches WatchService.create_from_intent.
# ---------------------------------------------------------------------------


def test_parse_and_book_threads_the_client_id_into_a_created_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.orchestrator.schemas import (
        IntentAction,
        IntentStatus,
        OrchestratorRoute,
        ReservationIntent,
    )

    app = create_app()
    history = RecordingHistory()
    queue = RecordingTaskQueue()
    service = WatchService(
        InMemoryWatchRepository(), EmptyAdapter(), queue, history=history
    )
    app.dependency_overrides[get_watch_service] = lambda: service

    class StubEngine:
        async def parse(self, prompt: str) -> ReservationIntent:
            return ReservationIntent(
                status=IntentStatus.READY,
                route=OrchestratorRoute.WATCH_SERVICE,
                action=IntentAction.CREATE_WATCH,
                venue_name="Cote",
                venue_type=VenueType.RESTAURANT,
                market="Kitchener-Waterloo-Cambridge, ON",
                party_size=4,
                date="2026-09-05",
                preferred_time="19:00",
                time_window=None,
                duration_minutes=None,
                special_requests=[],
                missing_fields=[],
                clarification_question=None,
            )

    from backend.main import get_orchestrator

    app.dependency_overrides[get_orchestrator] = lambda: StubEngine()

    with TestClient(app) as client:
        response = client.post(
            "/api/parse-and-book",
            json={"prompt": "watch Cote for 4"},
            headers={"X-Dibs-Client-Id": "visitor-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "WATCH_CREATED"
    watch_id = response.json()["watch_id"]
    assert history.records == [(watch_id, "visitor-1")]
