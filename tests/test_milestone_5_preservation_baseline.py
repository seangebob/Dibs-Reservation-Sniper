"""Preservation baselines Milestone 5 (accounts + auth) must not violate.

Characterization tests locking today's PRE-AUTHENTICATION contract on the
surfaces Milestone 5 touches:

- No route requires an ``Authorization`` header, and a bearer token is inert
  today -- the enduring guarantee (Req 7.2: no-``Authorization`` behavior stays
  byte-identical; Req 2.2: an invalid token degrades to anonymous, never errors).
- A watch is readable and cancellable by id with no auth and no owner check
  (Req 3.4: anonymous watches must remain reachable by id after M5 adds the
  account boundary).
- No ``/api/auth/*`` routes exist and CORS does not yet allow ``Authorization``.
  These document the starting point and are updated as Tasks 5/6 land, exactly
  as the Milestone 4 baseline updated its own "absence" assertions.

Deliberately overlaps other suites: the point of a baseline is to survive a
future refactor of the suites those assertions live in.
"""

import pytest
from fastapi.testclient import TestClient

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.main import _CORS_ALLOWED_HEADERS, create_app, get_watch_service
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


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

A_BEARER = {"Authorization": "Bearer some-token-that-does-not-exist"}


class _EmptyAdapter(MockBookingAdapter):
    """Never has availability, so a created watch stays ACTIVE (readable and
    cancellable). Duplicated on purpose so this baseline owns no infrastructure
    whose signatures Milestone 5 will change."""

    async def search_availability(self, query):  # noqa: ANN001
        return []


@pytest.fixture
def client():
    app = create_app()
    service = WatchService(
        InMemoryWatchRepository(), _EmptyAdapter(), RecordingTaskQueue()
    )
    app.dependency_overrides[get_watch_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Req 7.2: the whole anonymous watch lifecycle needs no Authorization header.
# ---------------------------------------------------------------------------


def test_anonymous_watch_lifecycle_needs_no_authorization(client: TestClient) -> None:
    created = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    )
    assert created.status_code == 201
    watch_id = created.json()["watch_id"]

    # Unscoped listing still shows it; "my watches" answers without erroring.
    assert watch_id in {w["watch_id"] for w in client.get("/api/watches").json()}
    assert client.get(
        "/api/watches/mine", headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).status_code == 200
    assert client.get("/api/watches/mine").status_code == 200


def test_read_and_cancel_by_id_need_no_auth_or_owner(client: TestClient) -> None:
    # Created under one client id...
    watch_id = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).json()["watch_id"]

    # ...read and cancelled by id with NO Authorization and NO matching client
    # id: the id alone is sufficient today (Req 3.4 keeps this for anon watches).
    assert client.get(f"/api/watches/{watch_id}").status_code == 200
    cancelled = client.delete(f"/api/watches/{watch_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


# ---------------------------------------------------------------------------
# Req 2.2 / 7.2: a bearer token is inert today. An unknown token must never
# turn an otherwise-anonymous request into an error on these routes.
# ---------------------------------------------------------------------------


def test_a_bearer_token_is_inert_on_existing_routes(client: TestClient) -> None:
    assert client.get("/health", headers=A_BEARER).status_code == 200
    created = client.post("/api/watches", json=QUERY, headers=A_BEARER)
    assert created.status_code == 201
    watch_id = created.json()["watch_id"]
    assert client.get(f"/api/watches/{watch_id}", headers=A_BEARER).status_code == 200


# ---------------------------------------------------------------------------
# Task 12 / Req 7.2, in its strongest form: not merely "still works" but
# BYTE-IDENTICAL. Every Milestone 1-4 route must answer exactly the same bytes
# whether or not an (unusable) Authorization header rides along, so no client
# can detect that authentication was ever added.
# ---------------------------------------------------------------------------


def test_reading_a_watch_is_byte_identical_with_and_without_a_bearer(
    client: TestClient,
) -> None:
    watch_id = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).json()["watch_id"]

    anonymous = client.get(f"/api/watches/{watch_id}")
    with_bearer = client.get(f"/api/watches/{watch_id}", headers=A_BEARER)

    assert anonymous.status_code == with_bearer.status_code == 200
    assert anonymous.content == with_bearer.content


@pytest.mark.parametrize("path", ["/health", "/api/watches", "/api/watches/mine"])
def test_collection_routes_are_byte_identical_with_and_without_a_bearer(
    client: TestClient, path: str
) -> None:
    client.post("/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"})

    assert client.get(path).content == client.get(path, headers=A_BEARER).content


def test_an_unknown_watch_is_still_a_plain_404_under_a_bearer(
    client: TestClient,
) -> None:
    """A missing watch must not become a 401/403 just because a token was sent."""

    anonymous = client.get("/api/watches/watch_ghost")
    with_bearer = client.get("/api/watches/watch_ghost", headers=A_BEARER)

    assert anonymous.status_code == with_bearer.status_code == 404
    assert anonymous.content == with_bearer.content


# ---------------------------------------------------------------------------
# Req 7.3: Milestone 5's ownership lives entirely in the history projection.
# The public `Watch` model must not have gained a field.
# ---------------------------------------------------------------------------

#: Exactly the Milestone 4 `Watch` interface in `frontend/types/api.ts`.
M4_WATCH_FIELDS = {
    "watch_id",
    "status",
    "query",
    "auto_book",
    "created_at",
    "updated_at",
    "expires_at",
    "attempts",
    "max_attempts",
    "last_checked_at",
    "next_check_at",
    "found_slots",
    "booking",
    "last_error",
}


def test_the_public_watch_body_gained_no_field_in_milestone_5(
    client: TestClient,
) -> None:
    body = client.post(
        "/api/watches", json=QUERY, headers={"X-Dibs-Client-Id": "visitor-alpha"}
    ).json()

    assert set(body) == M4_WATCH_FIELDS
    for leaked in ("user_id", "owner_client_id", "owner", "client_id", "email"):
        assert leaked not in body


# ---------------------------------------------------------------------------
# Updated by Task 5: /api/auth/* now exist, but with no PostgreSQL configured
# (this env) they degrade to 503 accounts-unavailable -- never 404, and never
# affecting the anonymous flow above (Req 6.2).
# ---------------------------------------------------------------------------


def test_auth_routes_report_unavailable_without_postgres(client: TestClient) -> None:
    creds = {"email": "a@x.com", "password": "hunter2-secret"}
    assert client.post("/api/auth/signup", json=creds).status_code == 503
    assert client.post("/api/auth/login", json=creds).status_code == 503
    assert client.post("/api/auth/logout").status_code == 503
    assert client.get("/api/auth/me").status_code == 503


# ---------------------------------------------------------------------------
# Updated by Task 6: CORS now allows Authorization so a browser can send a
# bearer token -- without disturbing the headers the anonymous flow needs.
# ---------------------------------------------------------------------------


def test_cors_allows_authorization_alongside_the_anonymous_headers() -> None:
    assert "Authorization" in _CORS_ALLOWED_HEADERS
    # The headers the anonymous flow already needs are still present.
    assert "Content-Type" in _CORS_ALLOWED_HEADERS
    assert "X-Dibs-Client-Id" in _CORS_ALLOWED_HEADERS
