"""Static assertions over the container / deployment assets.

These are pure text checks: no Docker daemon, no network, no live services.
They pin the contract for the root ``Dockerfile``, ``.dockerignore`` and the
additive ``app``-profiled compose services so a careless edit trips a test
rather than a broken image.

Compose is parsed with PyYAML when it is installed; otherwise the same
invariants are checked textually so the suite never grows a new dependency.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore_text() -> str:
    return DOCKERIGNORE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


# --- Dockerfile ------------------------------------------------------------


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.is_file()


def test_base_image_pins_a_specific_python_312_patch(dockerfile_text: str) -> None:
    # python:3.12.<patch>... -- a real patch tag, never 3.12 or latest.
    match = re.search(r"^FROM\s+python:3\.12\.\w+", dockerfile_text, re.MULTILINE)
    assert match, "Dockerfile must FROM a pinned python:3.12.<patch> tag"
    assert "python:3.12-" not in dockerfile_text
    assert "python:latest" not in dockerfile_text


def test_runs_as_a_non_root_user(dockerfile_text: str) -> None:
    users = re.findall(r"^USER\s+(\S+)", dockerfile_text, re.MULTILINE)
    assert users, "Dockerfile must set a USER directive"
    assert users[-1] not in {"root", "0"}, "final USER must be non-root"


def test_default_command_runs_uvicorn_without_reload(dockerfile_text: str) -> None:
    assert "backend.main:app" in dockerfile_text
    assert "uvicorn" in dockerfile_text
    assert "0.0.0.0" in dockerfile_text
    assert "8000" in dockerfile_text
    assert "--reload" not in dockerfile_text


def test_healthcheck_hits_the_health_endpoint(dockerfile_text: str) -> None:
    assert "HEALTHCHECK" in dockerfile_text
    assert "/health" in dockerfile_text


def test_worker_celery_command_is_documented_in_container_assets(
    dockerfile_text: str,
    compose_text: str,
) -> None:
    needle = "celery -A backend.workers.celery_app worker"
    assert needle in dockerfile_text or needle in compose_text


def test_image_does_not_install_the_test_extra(dockerfile_text: str) -> None:
    # Ignore comments -- only what the build actually executes matters.
    instructions = "\n".join(
        line
        for line in dockerfile_text.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "pytest" not in instructions
    assert "[test]" not in instructions
    assert ",test]" not in instructions


# --- .dockerignore ---------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [".git", ".venv", "tests", ".env", "__pycache__", ".kiro"],
)
def test_dockerignore_excludes_required_token(
    dockerignore_text: str,
    token: str,
) -> None:
    tokens = {
        line.strip()
        for line in dockerignore_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    # Match a whole entry, allowing a trailing-glob variant like ".env.*".
    assert any(entry == token or entry.startswith(token) for entry in tokens), (
        f"{token!r} must be excluded in .dockerignore"
    )


# --- Compose preservation + additive app services --------------------------

REDIS_HEALTHCHECK = ["CMD", "redis-cli", "ping"]
POSTGRES_HEALTHCHECK = ["CMD-SHELL", "pg_isready -U dibs"]


def _load_yaml(text: str):
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(text)


def test_compose_preserves_redis_and_postgres_and_adds_app_services(
    compose_text: str,
) -> None:
    parsed = _load_yaml(compose_text)

    if parsed is not None:
        services = parsed["services"]

        redis = services["redis"]
        assert redis["image"] == "redis:7-alpine"
        assert "6379:6379" in redis["ports"]
        assert redis["healthcheck"]["test"] == REDIS_HEALTHCHECK

        postgres = services["postgres"]
        assert postgres["image"] == "postgres:16-alpine"
        assert "5432:5432" in postgres["ports"]
        assert postgres["healthcheck"]["test"] == POSTGRES_HEALTHCHECK

        # api / worker exist ONLY under the "app" profile (not default-started).
        for name in ("api", "worker"):
            assert name in services, f"{name} service must be defined"
            assert services[name].get("profiles") == ["app"], (
                f"{name} must be gated behind profiles: [app]"
            )

        worker_cmd = services["worker"]["command"]
        assert "celery -A backend.workers.celery_app worker" in worker_cmd
        return

    # Fallback: PyYAML unavailable -- assert the same invariants textually.
    assert "image: redis:7-alpine" in compose_text
    assert "image: postgres:16-alpine" in compose_text
    assert '"6379:6379"' in compose_text
    assert '"5432:5432"' in compose_text
    assert '["CMD", "redis-cli", "ping"]' in compose_text
    assert '["CMD-SHELL", "pg_isready -U dibs"]' in compose_text
    for name in ("api:", "worker:"):
        assert name in compose_text
    assert 'profiles: ["app"]' in compose_text
    assert "celery -A backend.workers.celery_app worker" in compose_text
