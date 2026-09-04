"""Celery task that executes one watch poll."""

import asyncio
import atexit
from functools import lru_cache
import logging
from threading import Lock
from typing import Any

from celery.signals import worker_process_shutdown
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from backend.config import (
    ConfigurationError,
    EmailSettings,
    PostgresSettings,
    WatchSettings,
)
from backend.db.database import create_redis_client
from backend.db.postgres import create_pool
from backend.db.repositories.accounts import AccountRepository
from backend.db.repositories.mock_booking import RedisMockBookingStateRepository
from backend.db.repositories.watch_history import WatchHistoryRepository
from backend.db.repositories.watches import RedisWatchRepository
from backend.integrations.email import EmailNotificationService, SmtplibSender
from backend.integrations.mock_booking import MockBookingAdapter
from backend.services.notification_service import NotificationService
from backend.services.recipients import AccountRecipientResolver
from backend.services.watch_service import WatchService
from backend.workers.celery_app import celery_app
from backend.workers.queue import CeleryTaskQueue
from backend.workers.scheduler import PollSchedule


logger = logging.getLogger(__name__)
_runner_lock = Lock()
_resources_closed = False

#: The only failures worth retrying: the broker or Redis was briefly
#: unreachable, so the same poll can succeed unchanged a minute later.
#: Everything else -- a programming error, a validation failure, a provider
#: contract change -- fails identically on every delivery, so retrying it
#: only delays the traceback and holds a worker slot three times over.
_RECOVERABLE_INFRASTRUCTURE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    BrokerOperationalError,
)


@lru_cache(maxsize=1)
def _settings() -> WatchSettings:
    return WatchSettings.from_environment()


@lru_cache(maxsize=1)
def _runner() -> asyncio.Runner:
    """Keep one event loop for all tasks handled by this worker process."""

    return asyncio.Runner()


@lru_cache(maxsize=1)
def _redis_client() -> Any:
    return create_redis_client(_settings().redis_url)


@lru_cache(maxsize=1)
def _postgres_pool() -> Any:
    """The worker's own pool, or None when PostgreSQL is not configured.

    Deliberately does NOT take `_runner_lock`: its caller holds that while
    creating the pool on the runner's loop, and `_close_worker_resources` reads
    the already-cached value while holding it too. Locking here would deadlock
    shutdown, since `threading.Lock` is not reentrant.
    """

    try:
        settings = PostgresSettings.from_environment()
    except ConfigurationError as exc:
        logger.error(
            "PostgreSQL configuration is invalid; this worker's poll outcomes "
            "will not reach the durable projection: %s",
            str(exc),
        )
        return None
    if not settings.enabled:
        return None
    try:
        # On the runner's loop, so the pool's connections live where the polls
        # that use them run.
        return _runner().run(create_pool(settings))
    except Exception:
        logger.exception(
            "PostgreSQL is unreachable; this worker's poll outcomes will not "
            "reach the durable projection"
        )
        return None


def _build_notifier(pool: Any) -> NotificationService | None:
    """The same email notifier the API composes, or None to keep logging.

    Requires the pool: an address is resolved from the projection and the
    accounts table, so with no PostgreSQL there is nobody to email.
    """

    if pool is None:
        return None
    try:
        settings = EmailSettings.from_environment()
    except ConfigurationError as exc:
        logger.error(
            "Email settings invalid; this worker's notifications stay "
            "log-only: %s",
            str(exc),
        )
        return None
    if not settings.enabled:
        return None
    return EmailNotificationService(
        resolver=AccountRecipientResolver(
            WatchHistoryRepository(pool), AccountRepository(pool)
        ),
        sender=SmtplibSender(settings),
        dashboard_base_url=settings.dashboard_base_url,
    )


@lru_cache(maxsize=1)
def build_watch_service() -> WatchService:
    """Build one service whose async resources stay on the runner's loop.

    Milestone 6: this worker now composes the same durable projection and
    notifier the API process does. Before that it had neither, so every poll
    outcome discovered in the background updated no dashboard and told no one --
    even though `infra/docker-compose.yml` passes this worker `POSTGRES_URL`
    precisely so that it would.
    """

    settings = _settings()
    # The pool is created on the runner's loop, so this is serialized the same
    # way every other runner use is. `monitor_watch` calls this before taking
    # the lock itself, so the two acquisitions are sequential, never nested.
    with _runner_lock:
        pool = _postgres_pool()
    # The adapter is stateless; its state lives in Redis under the shared prefix,
    # so this worker books against the same store as the API and its siblings.
    mock_state = RedisMockBookingStateRepository(
        _redis_client(),
        capacity=settings.mock_slot_capacity,
        idle_ttl_seconds=settings.mock_slot_idle_ttl_seconds,
        retention_seconds=settings.mock_booking_retention_seconds,
    )
    return WatchService(
        RedisWatchRepository(
            _redis_client(),
            terminal_retention_seconds=settings.terminal_retention_seconds,
        ),
        MockBookingAdapter(state=mock_state),
        CeleryTaskQueue(monitor_watch),
        schedule=PollSchedule(
            interval_seconds=float(settings.poll_interval_seconds),
            jitter_seconds=float(settings.poll_jitter_seconds),
        ),
        history=WatchHistoryRepository(pool) if pool is not None else None,
        notifier=_build_notifier(pool),
        max_attempts=settings.max_poll_attempts,
        provider_timeout_seconds=settings.provider_call_timeout_seconds,
        backoff_max_seconds=settings.provider_backoff_max_seconds,
        timezone_name=settings.timezone_name,
    )


def _close_worker_resources() -> None:
    """Idempotently close resources on Celery and interpreter shutdown."""

    global _resources_closed
    with _runner_lock:
        if _resources_closed:
            return
        _resources_closed = True
        if _runner.cache_info().currsize == 0:
            return

        runner = _runner()
        try:
            if _redis_client.cache_info().currsize:
                runner.run(_redis_client().aclose())
        finally:
            try:
                # `currsize` first: calling the factory here would otherwise
                # build a pool during shutdown purely to close it. Safe to call
                # while holding `_runner_lock` because `_postgres_pool` does not
                # take it.
                if _postgres_pool.cache_info().currsize:
                    pool = _postgres_pool()
                    if pool is not None:
                        runner.run(pool.close())
            finally:
                runner.close()


@worker_process_shutdown.connect(weak=False)
def _close_on_worker_process_shutdown(**_kwargs: object) -> None:
    _close_worker_resources()


atexit.register(_close_worker_resources)


@celery_app.task(name="dibs.monitor_watch", bind=True, max_retries=3)
def monitor_watch(
    self, watch_id: str, window_id: str | None = None
) -> dict[str, object]:
    """Poll one watch and report what happened.

    `window_id` identifies the cadence window a delivery belongs to. When it is
    present the task takes the window-aware, claim-first service path; an
    already-queued one-argument job omits it and resolves the current window
    through `poll_once`. Both paths return the identical result shape.

    The task reschedules itself through the service, so a successful run either
    finishes the watch or leaves exactly one successor job on the queue. A
    broker- or Redis-level failure is retried when the service never reached a
    durable state transition; every other failure propagates on the first
    delivery so it surfaces as a traceback rather than three silent retries.
    Runner access is serialized for threaded Celery pools because
    asyncio.Runner cannot execute concurrent coroutines.
    """

    service = build_watch_service()
    try:
        with _runner_lock:
            if _resources_closed:
                raise RuntimeError("watch worker resources are already closed")
            result = _runner().run(
                service.poll_once(watch_id)
                if window_id is None
                else service.poll_window(watch_id, window_id)
            )
    except _RECOVERABLE_INFRASTRUCTURE_ERRORS as exc:
        logger.exception("monitor_watch failed for %s", watch_id)
        raise self.retry(exc=exc, countdown=60) from exc

    return {
        "watch_id": watch_id,
        "outcome": result.outcome.value,
        "retry_in_seconds": result.retry_in_seconds,
    }
