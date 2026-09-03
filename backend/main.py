"""FastAPI entry point for the Dibs MVP."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.client_identity import extract_client_id
from backend.api.dependencies import (
    AccountsUnavailableError,
    current_user,
    get_auth_service,
    get_booking_service,
    get_orchestrator,
    get_prompt_router,
    get_watch_service,
)
from backend.api.routes import auth_router, watches_router
from backend.config import (
    DEFAULT_MAX_POLL_ATTEMPTS,
    DEFAULT_MOCK_BOOKING_RETENTION_SECONDS,
    DEFAULT_MOCK_SLOT_CAPACITY,
    DEFAULT_MOCK_SLOT_IDLE_TTL_SECONDS,
    DEFAULT_PROVIDER_BACKOFF_MAX_SECONDS,
    DEFAULT_PROVIDER_CALL_TIMEOUT_SECONDS,
    AccountSettings,
    ConfigurationError,
    CorsSettings,
    PostgresSettings,
    Settings,
    WatchSettings,
)
from backend.db.postgres import create_pool, run_migrations
from backend.db.repositories.accounts import AccountRepository, SessionRepository
from backend.db.repositories.mock_booking import (
    MockBookingStateRepository,
    RedisMockBookingStateRepository,
    in_memory_mock_state,
)
from backend.db.repositories.watch_history import (
    TrackingHistoryRecorder,
    WatchHistoryRepository,
)
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
from backend.models.account import User
from backend.models.reservation import PromptExecutionResult
from backend.orchestrator.engine import OrchestratorEngine
from backend.orchestrator.providers import ProviderError, ProviderUnavailableError
from backend.orchestrator.router import PromptRouter
from backend.orchestrator.schemas import ParseRequest, ReservationIntent
from backend.services.auth_service import (
    AuthValidationError,
    AuthenticationRequiredError,
    AuthService,
    EmailTakenError,
    InvalidCredentialsError,
)
from backend.services.booking_service import BookingService
from backend.services.password import build_password_hasher
from backend.services.readiness import Readiness, ReadinessTracker
from backend.services.watch_recovery import RecoveryCoordinator
from backend.services.watch_service import WatchService
from backend.workers.dispatcher import WatchScheduleDispatcher
from backend.workers.queue import AsyncioTaskQueue, CeleryTaskQueue, TaskQueue
from backend.workers.scheduler import PollSchedule


logger = logging.getLogger(__name__)

__all__ = [
    "app",
    "create_app",
    "get_auth_service",
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
            str(exc),
        )

    try:
        app.state.settings = Settings.from_environment()
        app.state.settings_error = None
    except ConfigurationError as exc:
        app.state.settings = None
        app.state.settings_error = exc

    await _attach_postgres(app)
    await _attach_redis(app)

    yield

    await _stop_recovery(app)

    queue = app.state.watch_queue
    if queue is not None:
        await queue.close()

    redis_client = app.state.redis
    if redis_client is not None:
        await redis_client.aclose()

    postgres_pool = app.state.postgres_pool
    if postgres_pool is not None:
        await postgres_pool.close()

    engine: OrchestratorEngine | None = app.state.orchestrator
    if engine is not None:
        await engine.close()


def _bind_mock_adapter(app: FastAPI, state: MockBookingStateRepository) -> None:
    """Bind one shared mock state to the adapter every service in the process uses.

    `BookingService` and `WatchService` both read `app.state.booking_adapter`,
    so pointing that at an adapter over `state` makes the whole process observe
    one booking/idempotency store rather than diverging per-adapter dictionaries.
    """

    app.state.mock_booking_state = state
    adapter = MockBookingAdapter(state=state)
    app.state.booking_adapter = adapter
    app.state.booking_service = BookingService(adapter)


async def _attach_postgres(app: FastAPI) -> None:
    """Best-effort: connect PostgreSQL and migrate, or leave history disabled.

    Every failure here -- bad `POSTGRES_URL`, an unreachable server, a broken
    migration -- degrades to a disabled history projection with a logged
    error, never a startup failure. Unlike `WatchSettings`/`Settings`, nothing
    about the core watch or orchestrator routes depends on this: the durable
    history projection is optional, additive infrastructure for this
    milestone (design.md's "Key decision"; Requirement 3.1/3.2).
    """

    try:
        settings = PostgresSettings.from_environment()
    except ConfigurationError as exc:
        logger.error(
            "PostgreSQL configuration is invalid; watch history projection "
            "disabled: %s",
            str(exc),
        )
        return
    if not settings.enabled:
        return

    try:
        pool = await create_pool(settings)
        applied = await run_migrations(pool)
    except ConfigurationError as exc:
        logger.error(
            "PostgreSQL is unreachable; watch history projection disabled: %s",
            str(exc),
        )
        return

    if applied:
        logger.info("Applied PostgreSQL migrations: %s", ", ".join(applied))
    app.state.postgres_pool = pool
    # The raw repository serves `/api/watches/mine` reads; a tracking
    # decorator wraps only the writer path handed to `WatchService`, so every
    # projection write updates `history_readiness` on `/health` without
    # bloating the read path or making the raw repository harder to test.
    app.state.watch_history = WatchHistoryRepository(pool)
    app.state.watch_history_recorder = TrackingHistoryRecorder(
        app.state.watch_history, app.state.readiness
    )

    # Accounts live on the same pool. Built here so they exist only when
    # PostgreSQL does; bad account env degrades to accounts-disabled (503 on
    # /api/auth/*) rather than failing startup -- auth is an optional lens.
    try:
        account_settings = AccountSettings.from_environment()
        app.state.auth_service = AuthService(
            accounts=AccountRepository(pool),
            sessions=SessionRepository(pool),
            hasher=build_password_hasher(account_settings),
            settings=account_settings,
        )
    except ConfigurationError as exc:
        logger.error("Account settings invalid; accounts disabled: %s", str(exc))


async def _attach_redis(app: FastAPI) -> None:
    """Configure watch pacing, then upgrade storage and dispatch when possible."""

    settings: WatchSettings | None = app.state.watch_settings
    if settings is None:
        return

    schedule = PollSchedule(
        interval_seconds=float(settings.poll_interval_seconds),
        jitter_seconds=float(settings.poll_jitter_seconds),
    )

    # Apply watch settings even when local development has no infrastructure,
    # rebuilding the shared mock state with the configured bounds.
    _bind_mock_adapter(
        app,
        in_memory_mock_state(
            capacity=settings.mock_slot_capacity,
            idle_ttl_seconds=settings.mock_slot_idle_ttl_seconds,
            retention_seconds=settings.mock_booking_retention_seconds,
        ),
    )
    await app.state.watch_queue.close()
    app.state.watch_service = _build_watch_service(
        app,
        repository=app.state.watch_repository,
        schedule=schedule,
        max_attempts=settings.max_poll_attempts,
        timezone_name=settings.timezone_name,
        provider_timeout_seconds=settings.provider_call_timeout_seconds,
        backoff_max_seconds=settings.provider_backoff_max_seconds,
    )

    try:
        from backend.db.database import create_redis_client, ping
    except ModuleNotFoundError:
        logger.warning("redis is not installed; watches stay in process memory")
        await _start_recovery(app, settings, schedule, distributed=False)
        return

    client = create_redis_client(settings.redis_url)
    if not await ping(client):
        logger.warning(
            "Redis at %s is unreachable; watches stay in process memory",
            settings.redis_url,
        )
        await client.aclose()
        await _start_recovery(app, settings, schedule, distributed=False)
        return

    if await _redis_cluster_enabled(client):
        # The atomic multi-key Lua scripts assume every key for one watch (and
        # the shared indexes) lives on one node; Redis Cluster shards keys
        # across slots, so those scripts would fail or silently stop being
        # atomic. Standalone, a direct primary endpoint, and rediss:// are the
        # only supported topologies -- unsupported cluster mode is refused
        # exactly like an unreachable server, keeping watches in process memory.
        logger.warning(
            "Redis at %s reports cluster mode, which the atomic watch "
            "repository does not support; watches stay in process memory",
            settings.redis_url,
        )
        await client.aclose()
        await _start_recovery(app, settings, schedule, distributed=False)
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
    app.state.watch_repository = RedisWatchRepository(
        client, terminal_retention_seconds=settings.terminal_retention_seconds
    )
    # Share one Redis-backed mock state over the same client/prefix, so the API
    # and every worker child book against one store across processes.
    _bind_mock_adapter(
        app,
        RedisMockBookingStateRepository(
            client,
            capacity=settings.mock_slot_capacity,
            idle_ttl_seconds=settings.mock_slot_idle_ttl_seconds,
            retention_seconds=settings.mock_booking_retention_seconds,
        ),
    )
    app.state.watch_service = _build_watch_service(
        app,
        repository=app.state.watch_repository,
        schedule=schedule,
        max_attempts=settings.max_poll_attempts,
        timezone_name=settings.timezone_name,
        queue=queue,
        provider_timeout_seconds=settings.provider_call_timeout_seconds,
        backoff_max_seconds=settings.provider_backoff_max_seconds,
    )
    logger.info(
        "Watch state is backed by Redis at %s using the %s queue",
        settings.redis_url,
        app.state.watch_queue_mode,
    )
    await _start_recovery(app, settings, schedule, distributed=True)


def _queue_readiness(state: Any) -> Readiness:
    """The evidence-based readiness of the currently bound queue.

    Asyncio readiness is a live, always-knowable property of the bound queue
    object (open or closed on the running loop), so it is read directly here
    rather than through a possibly-stale recorded observation. Celery has no
    such live signal -- readiness there reflects only a performed broker
    dispatch, tracked by `ReadinessTracker` from the recovery/dispatch path.
    """

    if state.watch_queue_mode == "asyncio":
        queue = state.watch_queue
        is_ready = queue is not None and queue.is_ready()
        return Readiness.READY if is_ready else Readiness.DEGRADED
    return state.readiness.queue_readiness


async def _redis_cluster_enabled(client: Any) -> bool:
    """Whether the connected server reports Redis Cluster mode.

    A client that cannot answer `INFO` (a minimal test double, or a transient
    error right after a successful ping) is treated as not-cluster rather than
    failing startup here; the cross-slot Lua scripts either work correctly or
    surface their own errors later, but this probe never blocks the upgrade on
    its own account.
    """

    try:
        info = await client.info()
    except Exception:
        return False
    return bool(info.get("cluster_enabled"))


async def _start_recovery(
    app: FastAPI,
    settings: WatchSettings,
    schedule: PollSchedule,
    *,
    distributed: bool,
) -> None:
    """Build the recovery coordinator over the final bound components.

    Called exactly once, at the tail of whichever `_attach_redis` path the
    process took -- in-process fallback, a degraded/unreachable/cluster Redis,
    or the fully upgraded topology -- so recovery always reconciles the
    repository, queue, and mock state the application actually ended up using,
    never a component a later branch replaced.
    """

    dispatcher = WatchScheduleDispatcher(
        app.state.watch_repository,
        app.state.watch_queue,
        owner_id=app.state.recovery_owner_id,
        horizon_seconds=float(settings.dispatch_horizon_seconds),
    )
    coordinator = RecoveryCoordinator(
        app.state.watch_repository,
        dispatcher,
        owner_id=app.state.recovery_owner_id,
        distributed=distributed,
        leader_lease_seconds=float(settings.recovery_leader_lease_seconds),
        earliest_delay_seconds=schedule.earliest_delay,
        mock_state=app.state.mock_booking_state,
    )
    app.state.recovery_coordinator = coordinator
    app.state.readiness.record_recovery_outcome(await coordinator.reconcile_once())
    app.state.recovery_sweep_task = asyncio.create_task(
        _recovery_sweep_loop(
            coordinator, app.state.readiness, float(settings.recovery_sweep_seconds)
        )
    )


async def _recovery_sweep_loop(
    coordinator: RecoveryCoordinator,
    readiness: ReadinessTracker,
    sweep_seconds: float,
) -> None:
    """Run bounded follow-up passes while the application is up.

    A failed pass is logged and never propagates, so one bad sweep cannot kill
    the loop; the next sweep tries again on its own schedule.
    """

    while True:
        await asyncio.sleep(sweep_seconds)
        try:
            readiness.record_recovery_outcome(await coordinator.reconcile_once())
        except Exception:
            logger.exception("recovery follow-up sweep failed")


async def _stop_recovery(app: FastAPI) -> None:
    """Idempotently cancel the follow-up loop and release leadership."""

    task = app.state.recovery_sweep_task
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.recovery_sweep_task = None

    coordinator = app.state.recovery_coordinator
    if coordinator is not None:
        await coordinator.release()
        app.state.recovery_coordinator = None


def _build_watch_service(
    app: FastAPI,
    *,
    repository: object,
    schedule: PollSchedule | None = None,
    max_attempts: int | None = None,
    timezone_name: str | None = None,
    queue: TaskQueue | None = None,
    provider_timeout_seconds: float = DEFAULT_PROVIDER_CALL_TIMEOUT_SECONDS,
    backoff_max_seconds: float = DEFAULT_PROVIDER_BACKOFF_MAX_SECONDS,
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
        history=app.state.watch_history_recorder,
        timezone_name=timezone_name,
        max_attempts=(
            max_attempts if max_attempts is not None else DEFAULT_MAX_POLL_ATTEMPTS
        ),
        provider_timeout_seconds=provider_timeout_seconds,
        backoff_max_seconds=backoff_max_seconds,
    )


#: Only the headers Requirement 1/2's browser flow actually sends or must
#: read. `Content-Type` is required because JSON POST bodies are not a
#: CORS-safelisted content type; the policy/limit headers are exposed so a
#: browser client can read them via `fetch()`, since custom response headers
#: are invisible to JS unless explicitly exposed.
_CORS_ALLOWED_METHODS = ["GET", "POST", "DELETE"]
_CORS_ALLOWED_HEADERS = ["Content-Type", "X-Dibs-Client-Id", "Authorization"]
_CORS_EXPOSED_HEADERS = [
    "X-Watch-Monitoring-Policy",
    "X-Watch-Max-Availability-Checks",
    "Warning",
]


def _configure_cors(app: FastAPI) -> CorsSettings:
    """Attach `CORSMiddleware` if `FRONTEND_ORIGINS` is set, or stay disabled.

    Evaluated here rather than in `lifespan()`: Starlette forbids adding
    middleware once the ASGI app has been built, which happens on its first
    call, well before `lifespan()` runs. Every failure here -- a malformed
    origin -- degrades to CORS-disabled with a logged error rather than
    crashing startup, matching every other optional-feature failure mode in
    this module (`_attach_postgres`): a browser-CORS misconfiguration must
    never take down request-serving for non-browser callers.
    """

    try:
        settings = CorsSettings.from_environment()
    except ConfigurationError as exc:
        logger.error(
            "FRONTEND_ORIGINS configuration is invalid; CORS stays disabled: %s",
            str(exc),
        )
        return CorsSettings()

    if settings.enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.origins),
            allow_methods=_CORS_ALLOWED_METHODS,
            allow_headers=_CORS_ALLOWED_HEADERS,
            allow_credentials=False,
            expose_headers=_CORS_EXPOSED_HEADERS,
        )
    return settings


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
    app.state.cors_settings = _configure_cors(app)
    app.state.orchestrator = None
    app.state.orchestrator_lock = asyncio.Lock()
    app.state.settings = None
    app.state.settings_error = None
    app.state.watch_settings = None
    app.state.watch_settings_error = None
    app.state.redis = None
    app.state.postgres_pool = None
    app.state.watch_history = None
    app.state.watch_history_recorder = None
    app.state.auth_service = None
    app.state.watch_queue = None
    app.state.watch_queue_mode = "asyncio"
    app.state.recovery_owner_id = uuid.uuid4().hex
    app.state.recovery_coordinator = None
    app.state.recovery_sweep_task = None
    app.state.readiness = ReadinessTracker()

    _bind_mock_adapter(
        app,
        in_memory_mock_state(
            capacity=DEFAULT_MOCK_SLOT_CAPACITY,
            idle_ttl_seconds=DEFAULT_MOCK_SLOT_IDLE_TTL_SECONDS,
            retention_seconds=DEFAULT_MOCK_BOOKING_RETENTION_SECONDS,
        ),
    )
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

    @app.exception_handler(ProviderUnavailableError)
    async def provider_unavailable_handler(
        _request: Request,
        exc: ProviderUnavailableError,
    ) -> JSONResponse:
        # A 429 from the model (rate limit or exhausted credits) is transient
        # and retryable, so it surfaces as 503 -- distinct from the 502 a
        # genuine provider failure returns. Starlette dispatches to this more
        # specific handler before the ProviderError one below.
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
        queue_mode: str = request.app.state.watch_queue_mode
        return {
            "status": "ok",
            "service": "dibs-mvp",
            "config": "error" if error is not None else "ok",
            "watch_store": (
                "redis" if request.app.state.redis is not None else "memory"
            ),
            "watch_queue": queue_mode,
            "queue_readiness": _queue_readiness(request.app.state).value,
            "recovery_readiness": request.app.state.readiness.recovery_readiness.value,
            "history_readiness": request.app.state.readiness.history_readiness.value,
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
        user: Annotated[User | None, Depends(current_user)],
        x_dibs_client_id: Annotated[str | None, Header()] = None,
    ) -> PromptExecutionResult:
        intent = await engine.parse(request.prompt)
        owner_client_id = extract_client_id(x_dibs_client_id)
        return await router.execute(
            intent,
            owner_client_id=owner_client_id,
            user_id=user.id if user else None,
        )

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
    _auth_error_status = {
        EmailTakenError: status.HTTP_409_CONFLICT,
        InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
        AuthenticationRequiredError: status.HTTP_401_UNAUTHORIZED,
        AuthValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        AccountsUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    }

    def _auth_error_handler(status_code: int) -> Any:
        async def handler(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        return handler

    for _exc_cls, _code in _auth_error_status.items():
        app.add_exception_handler(_exc_cls, _auth_error_handler(_code))

    app.include_router(auth_router)
    app.include_router(watches_router)

    return app


app = create_app()
