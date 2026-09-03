"""Task 7: account ownership enforced through the history projection.

A valid bearer makes a created watch account-owned (user_id recorded, never on
the public `Watch`). `GET /api/watches/mine` scopes by account when authed;
`GET`/`DELETE /api/watches/{id}` 404 an account-owned watch for any other (or
no) account, indistinguishably from a missing one -- while anonymous-owned
watches keep the Milestone 1-4 by-id behavior for everyone (Req 3.1-3.5).

The projection is a fake in-memory store shared as both the WatchService
recorder and app.state.watch_history, so a create records into the same store
the enforcement gate reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import create_app, get_watch_service
from backend.models.account import User
from backend.models.watch import Watch
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

QUERY = {
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

USER_A = User(id=uuid4(), email="a@x.com", created_at=NOW)
USER_B = User(id=uuid4(), email="b@x.com", created_at=NOW)
BEARER_A = {"Authorization": "Bearer sess-A"}
BEARER_B = {"Authorization": "Bearer sess-B"}


class _EmptyAdapter(MockBookingAdapter):
    async def search_availability(self, query):  # noqa: ANN001
        return []


class _StubAuth:
    async def authenticate(self, raw_token: str | None) -> User | None:
        return {"sess-A": USER_A, "sess-B": USER_B}.get(raw_token or "")


class _FakeHistory:
    """In-memory projection: watch_id -> (watch, owner_client_id, user_id),
    preserving a recorded owner across a later ownerless record."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[Watch, str | None, UUID | None]] = {}

    async def record(self, watch, owner_client_id=None, user_id=None) -> None:
        prev = self.rows.get(watch.watch_id)
        if prev is not None:
            owner_client_id = owner_client_id or prev[1]
            user_id = user_id or prev[2]
        self.rows[watch.watch_id] = (watch, owner_client_id, user_id)

    async def get_account_owner(self, watch_id: str) -> UUID | None:
        row = self.rows.get(watch_id)
        return row[2] if row is not None else None

    async def list_for_user(self, user_id: UUID, *, limit: int = 100):
        return [r[0] for r in self.rows.values() if r[2] == user_id][:limit]

    async def list_for_owner(self, owner_client_id: str, *, limit: int = 100):
        return [r[0] for r in self.rows.values() if r[1] == owner_client_id][:limit]


@pytest.fixture
def client():
    history = _FakeHistory()
    app = create_app()
    app.state.auth_service = _StubAuth()
    app.state.watch_history = history
    service = WatchService(
        InMemoryWatchRepository(), _EmptyAdapter(), RecordingTaskQueue(),
        history=history,
    )
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client, history
    app.dependency_overrides.clear()


def _create(client: TestClient, headers=None) -> str:
    resp = client.post("/api/watches", json=QUERY, headers=headers or {})
    assert resp.status_code == 201
    return resp.json()["watch_id"]


# --- recording user_id -----------------------------------------------------


def test_an_authenticated_create_records_the_account_owner(client) -> None:
    tc, history = client
    watch_id = _create(tc, BEARER_A)
    assert history.rows[watch_id][2] == USER_A.id


def test_an_anonymous_create_records_no_account_owner(client) -> None:
    tc, history = client
    watch_id = _create(tc)
    assert history.rows[watch_id][2] is None


def test_user_id_never_appears_in_the_public_watch_body(client) -> None:
    tc, _ = client
    body = tc.post("/api/watches", json=QUERY, headers=BEARER_A).json()
    for forbidden in ("user_id", "owner_client_id", "owner"):
        assert forbidden not in body


# --- /mine scoping ---------------------------------------------------------


def test_mine_scopes_by_account_when_authenticated(client) -> None:
    tc, _ = client
    mine = _create(tc, BEARER_A)
    _create(tc, BEARER_B)  # someone else's

    resp = tc.get("/api/watches/mine", headers=BEARER_A)
    assert resp.status_code == 200
    assert [w["watch_id"] for w in resp.json()] == [mine]


# --- enforcement on GET/DELETE {id} ----------------------------------------


def test_owner_can_read_and_cancel_their_account_watch(client) -> None:
    tc, _ = client
    watch_id = _create(tc, BEARER_A)
    assert tc.get(f"/api/watches/{watch_id}", headers=BEARER_A).status_code == 200
    assert tc.delete(f"/api/watches/{watch_id}", headers=BEARER_A).status_code == 200


def test_another_account_gets_404_for_an_account_owned_watch(client) -> None:
    tc, _ = client
    watch_id = _create(tc, BEARER_A)
    assert tc.get(f"/api/watches/{watch_id}", headers=BEARER_B).status_code == 404
    assert tc.delete(f"/api/watches/{watch_id}", headers=BEARER_B).status_code == 404


def test_anonymous_gets_404_for_an_account_owned_watch(client) -> None:
    tc, _ = client
    watch_id = _create(tc, BEARER_A)
    assert tc.get(f"/api/watches/{watch_id}").status_code == 404
    assert tc.delete(f"/api/watches/{watch_id}").status_code == 404


def test_an_anonymous_watch_stays_reachable_by_id_for_everyone(client) -> None:
    tc, _ = client
    watch_id = _create(tc)  # no owner account
    # Anonymous and any account alike read it by id (Req 3.4).
    assert tc.get(f"/api/watches/{watch_id}").status_code == 200
    assert tc.get(f"/api/watches/{watch_id}", headers=BEARER_B).status_code == 200
    assert tc.delete(f"/api/watches/{watch_id}", headers=BEARER_B).status_code == 200
