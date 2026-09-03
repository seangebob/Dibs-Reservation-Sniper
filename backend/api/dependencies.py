"""Shared FastAPI dependencies.

Everything expensive -- the OpenAI client, the Redis client, the queue -- is
built once per process and cached on `app.state`, behind a lock so a burst of
concurrent first requests cannot build two of them.
"""

from typing import Annotated

from fastapi import Depends, Header, Request

from backend.config import ConfigurationError, Settings, WatchSettings
from backend.models.account import User
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import OpenAIIntentProvider
from backend.orchestrator.router import PromptRouter
from backend.services.auth_service import AuthenticationRequiredError, AuthService
from backend.services.booking_service import BookingService
from backend.services.watch_service import WatchService


class AccountsUnavailableError(RuntimeError):
    """Accounts require PostgreSQL, which is not configured (-> 503)."""


def bearer_token(authorization: str | None) -> str | None:
    """Extract the raw token from an ``Authorization: Bearer <token>`` header,
    or None when absent/malformed. Shared by the auth routes and the optional
    auth lens below."""

    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Optional auth lens: a valid bearer resolves to its user; a missing, bad,
    or expired token -- or accounts being unavailable -- is anonymous, never an
    error (Requirement 2.2). Task 7 attaches this to the watch routes."""

    service: AuthService | None = getattr(request.app.state, "auth_service", None)
    if service is None:
        return None
    return await service.authenticate(bearer_token(authorization))


async def require_user(
    user: Annotated[User | None, Depends(current_user)],
) -> User:
    """Like :func:`current_user`, but 401s instead of falling back to anonymous."""

    if user is None:
        raise AuthenticationRequiredError()
    return user


def get_auth_service(request: Request) -> AuthService:
    """Return the account service, or 503 when PostgreSQL is not configured.

    Accounts degrade rather than block: with no Postgres the anonymous flow is
    untouched and only /api/auth/* reports unavailable (Requirement 6.2)."""

    service: AuthService | None = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise AccountsUnavailableError(
            "Accounts are unavailable because PostgreSQL is not configured."
        )
    return service


async def get_orchestrator(request: Request) -> OrchestratorEngine:
    """Return one concurrency-safe orchestrator per application process."""

    engine: OrchestratorEngine | None = request.app.state.orchestrator
    if engine is not None:
        return engine

    async with request.app.state.orchestrator_lock:
        engine = request.app.state.orchestrator
        if engine is None:
            settings = request.app.state.settings or Settings.from_environment()
            provider = OpenAIIntentProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
            engine = OrchestratorEngine(
                provider,
                timezone_name=settings.timezone_name,
            )
            request.app.state.orchestrator = engine

    return engine


def get_booking_service(request: Request) -> BookingService:
    """Return the process-local service backed by the mock adapter."""

    return request.app.state.booking_service


def get_watch_service(request: Request) -> WatchService:
    """Return the configured watch service and its background queue.

    Settings are validated once during startup, so by the time a request
    arrives exactly one of two things is true: validation failed and the error
    was retained, or settings exist. Neither being true means startup did not
    run to completion, and the route below would silently fall back to UTC for
    its past-date comparison. That is a bug in our wiring rather than a
    configuration problem the caller can fix, so it fails loudly instead of
    reaching the route with a timezone nobody configured.
    """

    error: ConfigurationError | None = getattr(
        request.app.state,
        "watch_settings_error",
        None,
    )
    if error is not None:
        raise error

    settings: WatchSettings | None = getattr(
        request.app.state,
        "watch_settings",
        None,
    )
    if settings is None:
        raise RuntimeError(
            "watch settings are unavailable and no configuration error was "
            "retained; application startup did not complete"
        )
    return request.app.state.watch_service


def get_prompt_router(
    booking_service: Annotated[BookingService, Depends(get_booking_service)],
    watch_service: Annotated[WatchService, Depends(get_watch_service)],
) -> PromptRouter:
    """Compose the dispatcher from whichever services are currently bound.

    The router is a thin pairing of two services rather than a singleton, so
    overriding either dependency -- in a test, or later for a per-user
    adapter -- reaches the prompt endpoint too.
    """

    return PromptRouter(booking_service, watch_service)
