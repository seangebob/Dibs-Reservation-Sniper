"""FastAPI entry point for the Dibs MVP."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend.config import ConfigurationError, Settings
from backend.integrations.base import (
    AdapterError,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import PromptExecutionResult
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import OpenAIIntentProvider, ProviderError
from backend.orchestrator.schemas import ParseRequest, ReservationIntent
from backend.services.booking_service import BookingService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration on boot and release the client on shutdown.

    Configuration is read eagerly so an invalid model name or timezone is
    reported at startup, but a missing key is remembered rather than raised so
    the service can still answer with a clear 503 instead of refusing to boot.
    """

    try:
        app.state.settings = Settings.from_environment()
        app.state.settings_error = None
    except ConfigurationError as exc:
        app.state.settings = None
        app.state.settings_error = exc

    yield

    engine: OrchestratorEngine | None = app.state.orchestrator
    if engine is not None:
        await engine.close()


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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dibs MVP",
        version="0.3.0",
        description=(
            "Parses KW restaurant and recreation requests and runs deterministic "
            "mock availability or booking flows. No real provider is contacted."
        ),
        lifespan=lifespan,
    )
    app.state.orchestrator = None
    app.state.orchestrator_lock = asyncio.Lock()
    app.state.settings = None
    app.state.settings_error = None
    app.state.booking_service = BookingService(MockBookingAdapter())

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
        }

    async def parse_prompt(
        request: ParseRequest,
        engine: Annotated[OrchestratorEngine, Depends(get_orchestrator)],
    ) -> ReservationIntent:
        return await engine.parse(request.prompt)

    async def parse_and_book(
        request: ParseRequest,
        engine: Annotated[OrchestratorEngine, Depends(get_orchestrator)],
        booking_service: Annotated[BookingService, Depends(get_booking_service)],
    ) -> PromptExecutionResult:
        intent = await engine.parse(request.prompt)
        return await booking_service.execute(intent)

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
        summary="Parse a prompt and run the mock booking flow",
    )
    app.add_api_route(
        "/v1/intents/parse",
        parse_prompt,
        methods=["POST"],
        response_model=ReservationIntent,
        include_in_schema=False,
        deprecated=True,
    )

    return app


app = create_app()
