"""HTTP surface for watches, and the prompt path that opens one."""

from fastapi.testclient import TestClient
import pytest

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


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


@pytest.fixture
def queue() -> RecordingTaskQueue:
    return RecordingTaskQueue()


@pytest.fixture
def client(queue: RecordingTaskQueue):
    app = create_app()
    service = WatchService(InMemoryWatchRepository(), EmptyAdapter(), queue)
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_creating_a_watch_returns_201_and_queues_the_first_check(
    client: TestClient,
    queue: RecordingTaskQueue,
) -> None:
    response = client.post("/api/watches", json=QUERY)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["attempts"] == 0
    assert body["query"]["venue_name"] == "Cote"
    assert queue.dispatches == [(body["watch_id"], 0.0)]


def test_watches_can_be_listed_and_read_back(client: TestClient) -> None:
    watch_id = client.post("/api/watches", json=QUERY).json()["watch_id"]

    listed = client.get("/api/watches")
    read = client.get(f"/api/watches/{watch_id}")

    assert [record["watch_id"] for record in listed.json()] == [watch_id]
    assert read.status_code == 200
    assert read.json()["watch_id"] == watch_id


def test_listing_can_be_narrowed_to_active_watches(client: TestClient) -> None:
    first = client.post("/api/watches", json=QUERY).json()["watch_id"]
    client.post("/api/watches", json={**QUERY, "venue_name": "Bhima's Warung"})
    client.delete(f"/api/watches/{first}")

    assert len(client.get("/api/watches").json()) == 2
    assert len(client.get("/api/watches", params={"active_only": True}).json()) == 1


def test_cancelling_a_watch_marks_it_cancelled(client: TestClient) -> None:
    watch_id = client.post("/api/watches", json=QUERY).json()["watch_id"]

    response = client.delete(f"/api/watches/{watch_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["next_check_at"] is None


def test_unknown_watch_is_a_404(client: TestClient) -> None:
    assert client.get("/api/watches/watch_missing").status_code == 404
    assert client.delete("/api/watches/watch_missing").status_code == 404


def test_a_malformed_query_is_rejected_before_anything_is_queued(
    client: TestClient,
    queue: RecordingTaskQueue,
) -> None:
    response = client.post("/api/watches", json={**QUERY, "party_size": 0})

    assert response.status_code == 422
    assert queue.dispatches == []


def test_a_query_without_any_time_preference_is_rejected(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/watches",
        json={**QUERY, "preferred_time": None, "time_window": None},
    )

    assert response.status_code == 422


def test_auto_book_is_recorded_on_the_watch(client: TestClient) -> None:
    response = client.post(
        "/api/watches",
        json=QUERY,
        params={"auto_book": True},
    )

    assert response.json()["auto_book"] is True


def test_the_default_app_wires_a_real_watch_service_without_overrides() -> None:
    app = create_app()

    with TestClient(app) as client:
        created = client.post("/api/watches", json=QUERY)

    assert created.status_code == 201
    # The default in-process queue polls immediately, and the mock adapter has
    # a slot, so the watch is already resolved by the time we read it back.
    assert created.json()["watch_id"].startswith("watch_")
