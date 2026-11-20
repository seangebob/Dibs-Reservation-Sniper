"""`LLM_PROVIDER` decides which model reads prompts, end to end through the API."""

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.api import dependencies
from backend.main import create_app
from backend.orchestrator.providers import LocalIntentProvider, OpenAIIntentProvider
from backend.orchestrator.schemas import (
    IntentAction,
    ReservationExtraction,
    VenueType,
)


PROMPT = {"prompt": "Book The Bauer Kitchen for four in three days at 7 pm"}


def _future_date() -> str:
    return (datetime.now(ZoneInfo("America/Toronto")) + timedelta(days=3)).date().isoformat()


class FakeLocalProvider:
    """Stands in for LocalIntentProvider so no server or model is needed."""

    instances: list["FakeLocalProvider"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.prompts: list[str] = []
        FakeLocalProvider.instances.append(self)

    async def extract(self, prompt: str, reference_time: datetime) -> ReservationExtraction:
        self.prompts.append(prompt)
        return ReservationExtraction(
            action=IntentAction.BOOK_RESERVATION,
            venue_name="The Bauer Kitchen",
            venue_type=VenueType.RESTAURANT,
            party_size=4,
            date=_future_date(),
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        )

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_local(monkeypatch: pytest.MonkeyPatch) -> type[FakeLocalProvider]:
    FakeLocalProvider.instances = []
    monkeypatch.setattr(dependencies, "LocalIntentProvider", FakeLocalProvider)
    return FakeLocalProvider


def test_local_provider_parses_prompts_without_an_openai_key(
    monkeypatch: pytest.MonkeyPatch,
    fake_local: type[FakeLocalProvider],
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.5:9b")

    with TestClient(create_app()) as client:
        assert client.get("/health").json()["config"] == "ok"
        response = client.post("/api/orchestrator/parse", json=PROMPT)

    assert response.status_code == 200
    assert response.json()["status"] == "READY"
    (provider,) = fake_local.instances
    assert provider.kwargs == {
        "model": "qwen3.5:9b",
        "base_url": "http://localhost:11434/v1",
    }
    assert provider.prompts == [PROMPT["prompt"]]


def test_local_provider_books_through_parse_and_book(
    monkeypatch: pytest.MonkeyPatch,
    fake_local: type[FakeLocalProvider],
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "local")

    with TestClient(create_app()) as client:
        response = client.post("/api/parse-and-book", json=PROMPT)

    assert response.status_code == 200
    assert response.json()["status"] == "MOCK_BOOKED"


def test_openai_is_built_when_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = create_app()

    with TestClient(app):
        settings = app.state.settings
        provider = dependencies.build_intent_provider(settings)

    assert isinstance(provider, OpenAIIntentProvider)


def test_local_setting_builds_the_real_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
    app = create_app()

    with TestClient(app):
        provider = dependencies.build_intent_provider(app.state.settings)

    assert isinstance(provider, LocalIntentProvider)
    assert str(provider._client.base_url).rstrip("/") == (
        "http://host.docker.internal:11434/v1"
    )


def test_unreachable_local_server_is_a_503_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing listens on port 1, so the real client fails at once.
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1/v1")

    with TestClient(create_app()) as client:
        response = client.post("/api/orchestrator/parse", json=PROMPT)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_invalid_provider_setting_reports_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "olama")

    with TestClient(create_app()) as client:
        assert client.get("/health").json()["config"] == "error"
        response = client.post("/api/orchestrator/parse", json=PROMPT)

    assert response.status_code == 503
    assert "LLM_PROVIDER" in response.json()["detail"]


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        (
            {"LLM_PROVIDER": "local"},
            "Prompts are read by local model 'qwen3.5:9b' at http://localhost:11434/v1",
        ),
        (
            {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"},
            "Prompts are read by OpenAI model 'gpt-4o-mini'",
        ),
    ],
    ids=["local", "openai"],
)
def test_startup_logs_which_model_reads_prompts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    environment: dict[str, str],
    expected: str,
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with caplog.at_level(logging.INFO, logger="backend.main"):
        with TestClient(create_app()):
            pass

    assert expected in caplog.text
