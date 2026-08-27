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

from backend.config import WatchSettings
from backend.db.database import create_redis_client
from backend.db.repositories.watches import RedisWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
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
def build_watch_service() -> WatchService:
    """Build one service whose async resources stay on the runner's loop."""

    settings = _settings()
    return WatchService(
        RedisWatchRepository(_redis_client()),
        MockBookingAdapter(),
        CeleryTaskQueue(monitor_watch),
        schedule=PollSchedule(
            interval_seconds=float(settings.poll_interval_seconds),
            jitter_seconds=float(settings.poll_jitter_seconds),
        ),
        max_attempts=settings.max_poll_attempts,
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
            runner.close()


@worker_process_shutdown.connect(weak=False)
def _close_on_worker_process_shutdown(**_kwargs: object) -> None:
    _close_worker_resources()


atexit.register(_close_worker_resources)


@celery_app.task(name="dibs.monitor_watch", bind=True, max_retries=3)
def monitor_watch(self, watch_id: str) -> dict[str, object]:
    """Poll one watch and report what happened.

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
            result = _runner().run(service.poll_once(watch_id))
    except _RECOVERABLE_INFRASTRUCTURE_ERRORS as exc:
        logger.exception("monitor_watch failed for %s", watch_id)
        raise self.retry(exc=exc, countdown=60) from exc

    return {
        "watch_id": watch_id,
        "outcome": result.outcome.value,
        "retry_in_seconds": result.retry_in_seconds,
    }
