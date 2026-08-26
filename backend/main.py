"""FastAPI entry point for the Dibs MVP."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend.api.dependencies import (
    get_booking_service,
    get_orchestrator,
    get_prompt_router,
    get_watch_service,
)
from backend.api.routes import watches_router
from backend.config import ConfigurationError, Settings, WatchSettings
from backend.db.repositories.watches import (
    InMemoryWatchRepository,
    RedisWatchRepository,
)
from backend.integrations.base import (
    AdapterError,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.integrations.mock_booking import MockBookingAdapter
from backend.logging_config import configure_application_logging
from backend.models.reservation import PromptExecutionResult
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import OpenAIIntentProvider, ProviderError
from backend.orchestrator.router import PromptRouter
from backend.orchestrator.schemas import ParseRequest, ReservationIntent
from backend.services.booking_service import BookingService
from backend.services.watch_service import WatchService
from backend.workers.queue import AsyncioTaskQueue, CeleryTaskQueue, TaskQueue
from backend.workers.scheduler import PollSchedule


logger = logging.getLogger(__name__)

__all__ = [
    "app",
    "create_app",
    "get_booking_service",
    "get_orchestrator",
    "get_prompt_router",
    "get_watch_service",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration on boot and release clients on shutdown."""

    configure_application_logging()

    try:
        app.state.watch_settings = WatchSettings.from_environment()
        app.state.watch_settings_error = None
    except ConfigurationError as exc:
        app.state.watch_settings = None
        app.state.watch_settings_error = exc
        logger.error(
            "Watch settings validation failed during startup; "
            "watch-dependent requests will return 503: %s",
            exc,
        )

    try:
        app.state.settings = Settings.from_environment()
        app.state.settings_error = None
    except ConfigurationError as exc:
        app.state.settings = None
        app.state.settings_error = exc

    await _attach_redis(app)

    yield

    queue = app.state.watch_queue
    if queue is not None:
        await queue.close()

    redis_client = app.state.redis
    if redis_client is not None:
        await redis_client.aclose()

    engine: OrchestratorEngine | None = app.state.orchestrator
    if engine is not None:
        await engine.close()


async def _attach_redis(app: FastAPI) -> None:
    """Configure watch pacing, then upgrade storage and dispatch when possible."""

    settings: WatchSettings | None = app.state.watch_settings
    if settings is None:
        return

    schedule = PollSchedule(
        interval_seconds=float(settings.poll_interval_seconds),
        jitter_seconds=float(settings.poll_jitter_seconds),
    )

    # Apply watch settings even when local development has no infrastructure.
    await app.state.watch_queue.close()
    app.state.watch_service = _build_watch_service(
        app,
        repository=app.state.watch_repository,
        schedule=schedule,
        max_attempts=settings.max_poll_attempts,
        timezone_name=settings.timezone_name,
    )

    try:
        from backend.db.database import create_redis_client, ping
    except ModuleNotFoundError:
        logger.warning("redis is not installed; watches stay in process memory")
        return

    client = create_redis_client(settings.redis_url)
    if not await ping(client):
        logger.warning(
            "Redis at %s is unreachable; watches stay in process memory",
            settings.redis_url,
        )
        await client.aclose()
        return

    queue: TaskQueue | None = None
    try:
        from backend.workers.tasks.monitor_watch import monitor_watch

        queue = CeleryTaskQueue(monitor_watch)
        app.state.watch_queue_mode = "celery"
    except ModuleNotFoundError:
        # Celery is an optional extra. Redis can still improve state durability
        # while local polling remains in-process when the worker is not installed.
        logger.warning(
            "Celery is not installed; Redis state uses the in-process queue"
        )
        app.state.watch_queue_mode = "asyncio"

    await app.state.watch_queue.close()
    app.state.redis = client
    app.state.watch_repository = RedisWatchRepository(client)
    app.state.watch_service = _build_watch_service(
        app,
        repository=app.state.watch_repository,
        schedule=schedule,
        max_attempts=settings.max_poll_attempts,
        timezone_name=settings.timezone_name,
        queue=queue,
    )
    logger.info(
        "Watch state is backed by Redis at %s using the %s queue",
        settings.redis_url,
        app.state.watch_queue_mode,
    )


def _build_watch_service(
    app: FastAPI,
    *,
    repository: object,
    schedule: PollSchedule | None = None,
    max_attempts: int | None = None,
    timezone_name: str | None = None,
    queue: TaskQueue | None = None,
) -> WatchService:
    """Build a watch service and its selected dispatch boundary."""

    if queue is None:
        async def poll(watch_id: str) -> None:
            await app.state.watch_service.poll_once(watch_id)

        queue = AsyncioTaskQueue(poll)
        app.state.watch_queue_mode = "asyncio"

    app.state.watch_queue = queue
    return WatchService(
        repository,
        app.state.booking_adapter,
        queue,
        schedule=schedule,
        timezone_name=timezone_name,
        **({"max_attempts": max_attempts} if max_attempts is not None else {}),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dibs MVP",
        version="0.4.0",
        description=(
            "Parses KW restaurant and recreation requests, runs deterministic "
            "mock availability or booking flows, and monitors unavailable "
            "slots on a jittered background queue. No real provider is "
            "contacted."
        ),
        lifespan=lifespan,
    )
    app.state.orchestrator = None
    app.state.orchestrator_lock = asyncio.Lock()
    app.state.settings = None
    app.state.settings_error = None
    app.state.watch_settings = None
    app.state.watch_settings_error = None
    app.state.redis = None
    app.state.watch_queue = None
    app.state.watch_queue_mode = "asyncio"

    adapter = MockBookingAdapter()
    app.state.booking_adapter = adapter
    app.state.booking_service = BookingService(adapter)
    app.state.watch_repository = InMemoryWatchRepository()
    app.state.watch_service = _build_watch_service(
        app,
        repository=app.state.watch_repository,
    )

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(
        _request: Request,
        exc: ConfigurationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(
        _request: Request,
        exc: ProviderError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SlotNotFoundError)
    async def slot_not_found_handler(
        _request: Request,
        exc: SlotNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SlotUnavailableError)
    async def slot_unavailable_handler(
        _request: Request,
        exc: SlotUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AdapterError)
    async def adapter_error_handler(
        _request: Request,
        exc: AdapterError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, str]:
        error: ConfigurationError | None = request.app.state.settings_error
        return {
            "status": "ok",
            "service": "dibs-mvp",
            "config": "error" if error is not None else "ok",
            "watch_store": (
                "redis" if request.app.state.redis is not None else "memory"
            ),
        }

    async def parse_prompt(
        request: ParseRequest,
        engine: Annotated[OrchestratorEngine, Depends(get_orchestrator)],
    ) -> ReservationIntent:
        return await engine.parse(request.prompt)

    async def parse_and_book(
        request: ParseRequest,
        engine: Annotated[OrchestratorEngine, Depends(get_orchestrator)],
        router: Annotated[PromptRouter, Depends(get_prompt_router)],
    ) -> PromptExecutionResult:
        intent = await engine.parse(request.prompt)
        return await router.execute(intent)

    app.add_api_route(
        "/api/orchestrator/parse",
        parse_prompt,
        methods=["POST"],
        response_model=ReservationIntent,
        tags=["orchestrator"],
    )
    app.add_api_route(
        "/api/parse-and-book",
        parse_and_book,
        methods=["POST"],
        response_model=PromptExecutionResult,
        tags=["booking"],
        summary="Parse a prompt, then book, search, or open a watch",
    )
    app.add_api_route(
        "/v1/intents/parse",
        parse_prompt,
        methods=["POST"],
        response_model=ReservationIntent,
        include_in_schema=False,
        deprecated=True,
    )
    app.include_router(watches_router)

    return app


app = create_app()
