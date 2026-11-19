"""GET /api/venues: the catalog the manual watch form suggests from."""

from fastapi.testclient import TestClient

from backend.data.venues import VENUE_CATALOG
from backend.main import create_app


def test_lists_every_catalog_venue_with_its_type_and_aliases() -> None:
    # No OpenAI key, Redis, or PostgreSQL: the catalog needs none of them.
    with TestClient(create_app()) as client:
        response = client.get("/api/venues")

    assert response.status_code == 200
    venues = response.json()
    assert [venue["name"] for venue in venues] == [v.name for v in VENUE_CATALOG]
    by_name = {venue["name"]: venue for venue in venues}
    assert by_name["The Bauer Kitchen"]["venue_type"] == "RESTAURANT"
    assert by_name["Grand River Rocks"] == {
        "name": "Grand River Rocks",
        "venue_type": "RECREATION",
        "aliases": ["grr", "grand river rocks climbing gym"],
    }


def test_is_readable_cross_origin_from_the_frontend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:3000")

    with TestClient(create_app()) as client:
        response = client.get(
            "/api/venues", headers={"Origin": "http://localhost:3000"}
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
