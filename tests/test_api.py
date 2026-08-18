from fastapi.testclient import TestClient

from reservation_nlp.api import app, get_parser
from reservation_nlp.models import ReservationIntent


class StubParser:
    def __init__(self, result: ReservationIntent) -> None:
        self.result = result
        self.prompt: str | None = None

    async def parse(self, prompt: str) -> ReservationIntent:
        self.prompt = prompt
        return self.result


def test_parse_endpoint_returns_clean_complete_json() -> None:
    parser = StubParser(
        ReservationIntent(
            restaurant="Cote",
            party_size=4,
            date="2026-08-22",
            preferred_time="19:00",
            missing_info=None,
        )
    )
    app.dependency_overrides[get_parser] = lambda: parser

    try:
        response = TestClient(app).post(
            "/v1/intents/parse",
            json={"prompt": "Book Cote for four next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "restaurant": "Cote",
        "party_size": 4,
        "date": "2026-08-22",
        "preferred_time": "19:00",
        "missing_info": None,
    }
    assert parser.prompt == "Book Cote for four next Saturday at 7 pm"


def test_parse_endpoint_returns_clarification_json() -> None:
    parser = StubParser(
        ReservationIntent(
            restaurant="Cote",
            party_size=None,
            date="2026-08-22",
            preferred_time="19:00",
            missing_info="How many people are in your party?",
        )
    )
    app.dependency_overrides[get_parser] = lambda: parser

    try:
        response = TestClient(app).post(
            "/v1/intents/parse",
            json={"prompt": "Cote next Saturday at 7 pm"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["party_size"] is None
    assert response.json()["missing_info"] == "How many people are in your party?"


def test_parse_endpoint_rejects_empty_prompt_before_provider_call() -> None:
    parser = StubParser(
        ReservationIntent(
            restaurant="unused",
            party_size=1,
            date="2026-08-22",
            preferred_time="19:00",
            missing_info=None,
        )
    )
    app.dependency_overrides[get_parser] = lambda: parser

    try:
        response = TestClient(app).post(
            "/v1/intents/parse",
            json={"prompt": "  "},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert parser.prompt is None
