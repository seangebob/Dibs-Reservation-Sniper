"""Task 6: `CorsSettings` validation and the wired `CORSMiddleware`.

`FRONTEND_ORIGINS` unset is the default in every other test file in this
suite (never explicitly cleared there), which is exactly what proves
Requirement 5.2: a frontend-less backend is unaffected. These tests instead
deliberately set/unset it to exercise the feature itself.
"""

from fastapi.testclient import TestClient
import pytest

from backend.config import ConfigurationError, CorsSettings
from backend.main import create_app


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)


# ---------------------------------------------------------------------------
# CorsSettings.from_environment
# ---------------------------------------------------------------------------


def test_unset_frontend_origins_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)

    settings = CorsSettings.from_environment()

    assert settings.enabled is False
    assert settings.origins == ()


def test_a_single_valid_origin_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")

    settings = CorsSettings.from_environment()

    assert settings.enabled is True
    assert settings.origins == ("https://app.example.com",)


def test_multiple_comma_separated_origins_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(
        "FRONTEND_ORIGINS", "http://localhost:3000, https://app.example.com"
    )

    settings = CorsSettings.from_environment()

    assert settings.origins == ("http://localhost:3000", "https://app.example.com")


def test_an_explicitly_empty_value_fails_loudly_rather_than_silently_disabling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "   ")

    with pytest.raises(ConfigurationError, match="contains no origins"):
        CorsSettings.from_environment()


@pytest.mark.parametrize(
    "origin",
    [
        "not-a-url",
        "ftp://example.com",
        "app.example.com",
        "https://",
    ],
)
def test_a_malformed_origin_is_rejected(
    origin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", origin)

    with pytest.raises(ConfigurationError, match="Invalid origin"):
        CorsSettings.from_environment()


def test_an_origin_with_a_path_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com/some/path")

    with pytest.raises(ConfigurationError, match="must not include a path"):
        CorsSettings.from_environment()


# ---------------------------------------------------------------------------
# Wired CORSMiddleware, via TestClient
# ---------------------------------------------------------------------------


def test_a_configured_origin_receives_cors_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://app.example.com"}
        )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://app.example.com"
    )


def test_an_unconfigured_origin_receives_no_cors_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://evil.example.com"}
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_preflight_for_a_configured_origin_allows_the_client_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")
    app = create_app()

    with TestClient(app) as client:
        response = client.options(
            "/api/watches",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-dibs-client-id",
            },
        )

    assert response.status_code == 200
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-dibs-client-id" in allowed_headers
    assert "content-type" in allowed_headers


def test_credentials_are_never_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://app.example.com"}
        )

    assert "access-control-allow-credentials" not in response.headers


def test_the_policy_headers_are_exposed_for_javascript_to_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.com")
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://app.example.com"}
        )

    exposed = response.headers["access-control-expose-headers"]
    assert "X-Watch-Monitoring-Policy" in exposed


def test_a_malformed_frontend_origins_disables_cors_without_crashing_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("FRONTEND_ORIGINS", "not-a-url")
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://app.example.com"}
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert app.state.cors_settings.enabled is False


def test_no_frontend_origins_means_no_cors_headers_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5.2: a frontend-less backend is entirely unaffected."""

    _clear(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health", headers={"Origin": "https://app.example.com"}
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
