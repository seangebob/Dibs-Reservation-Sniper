"""Shared FastAPI dependencies.

Everything expensive -- the OpenAI client, the Redis client, the queue -- is
built once per process and cached on `app.state`, behind a lock so a burst of
concurrent first requests cannot build two of them.
"""

from typing import Annotated

from fastapi import Depends, Request

from backend.config import Settings
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import OpenAIIntentProvider
from backend.orchestrator.router import PromptRouter
from backend.services.booking_service import BookingService
from backend.services.watch_service import WatchService


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
    """Return the process-local watch service and its background queue."""

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
