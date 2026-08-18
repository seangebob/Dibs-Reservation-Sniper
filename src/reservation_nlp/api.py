"""FastAPI entry point for Milestone 1."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from reservation_nlp.config import ConfigurationError, Settings
from reservation_nlp.models import ParseRequest, ReservationIntent
from reservation_nlp.providers import OpenAIIntentProvider, ProviderError
from reservation_nlp.service import ReservationParser


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Close the shared provider client when the application stops."""

    yield
    parser: ReservationParser | None = app.state.parser
    if parser is not None:
        await parser.close()


async def get_parser(request: Request) -> ReservationParser:
    """Return one concurrency-safe parser instance per application process."""

    parser: ReservationParser | None = request.app.state.parser
    if parser is not None:
        return parser

    async with request.app.state.parser_lock:
        parser = request.app.state.parser
        if parser is None:
            settings = Settings.from_environment()
            provider = OpenAIIntentProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
            parser = ReservationParser(
                provider,
                timezone_name=settings.timezone_name,
            )
            request.app.state.parser = parser

    return parser


def create_app() -> FastAPI:
    app = FastAPI(
        title="Reservation Intent Parser",
        version="0.1.0",
        description="Converts raw reservation prompts into validated structured data.",
        lifespan=lifespan,
    )
    app.state.parser = None
    app.state.parser_lock = asyncio.Lock()

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

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/intents/parse",
        response_model=ReservationIntent,
        tags=["intents"],
    )
    async def parse_intent(
        request: ParseRequest,
        parser: Annotated[ReservationParser, Depends(get_parser)],
    ) -> ReservationIntent:
        return await parser.parse(request.prompt)

    return app


app = create_app()
