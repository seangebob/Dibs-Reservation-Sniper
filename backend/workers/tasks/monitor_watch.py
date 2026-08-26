"""Celery task that executes one watch poll."""

import asyncio
import atexit
from functools import lru_cache
import logging

from backend.config import WatchSettings
from backend.db.database import create_redis_client
from backend.db.repositories.watches import RedisWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.services.watch_service import WatchService
from backend.workers.celery_app import celery_app
from backend.workers.queue import CeleryTaskQueue
from backend.workers.scheduler import PollSchedule


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _settings() -> WatchSettings:
    return WatchSettings.from_environment()


@lru_cache(maxsize=1)
def _runner() -> asyncio.Runner:
    """Keep one event loop for all tasks handled by this worker process."""

    return asyncio.Runner()


@lru_cache(maxsize=1)
def _redis_client():  # noqa: ANN202 - redis-py exposes a generic client here
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
    """Close the cached Redis pool and persistent event loop at process exit."""

    if _runner.cache_info().currsize == 0:
        return
    runner = _runner()
    if _redis_client.cache_info().currsize:
        runner.run(_redis_client().aclose())
    runner.close()


atexit.register(_close_worker_resources)


@celery_app.task(name="dibs.monitor_watch", bind=True, max_retries=3)
def monitor_watch(self, watch_id: str) -> dict[str, object]:
    """Poll one watch and report what happened.

    The task reschedules itself through the service, so a successful run either
    finishes the watch or leaves exactly one successor job on the queue. A
    broker- or Redis-level failure is retried when the service never reached a
    durable state transition.
    """

    service = build_watch_service()
    try:
        result = _runner().run(service.poll_once(watch_id))
    except Exception as exc:  # noqa: BLE001 - retry infrastructure failures
        logger.exception("monitor_watch failed for %s", watch_id)
        raise self.retry(exc=exc, countdown=60) from exc

    return {
        "watch_id": watch_id,
        "outcome": result.outcome.value,
        "retry_in_seconds": result.retry_in_seconds,
    }
