"""The Celery task that runs one availability check for a watch.

This module is a thin adapter: it owns process-local wiring (Redis client,
adapter, queue) and hands the actual decision to `WatchService.poll_once`, so
the polling contract stays testable without a broker.
"""

import asyncio
import logging
from functools import lru_cache

from backend.config import Settings
from backend.db.database import create_redis_client
from backend.db.repositories.watches import RedisWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.services.watch_service import WatchService
from backend.workers.celery_app import celery_app
from backend.workers.queue import CeleryTaskQueue
from backend.workers.scheduler import PollSchedule


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings.from_environment()


@lru_cache(maxsize=1)
def build_watch_service() -> WatchService:
    """Build the worker's service once per process.

    Celery workers are long-lived processes, so the Redis client and adapter
    are cached rather than rebuilt for every job.
    """

    settings = _settings()
    return WatchService(
        RedisWatchRepository(create_redis_client(settings.redis_url)),
        MockBookingAdapter(),
        CeleryTaskQueue(monitor_watch),
        schedule=PollSchedule(
            interval_seconds=float(settings.poll_interval_seconds),
            jitter_seconds=float(settings.poll_jitter_seconds),
        ),
        max_attempts=settings.max_poll_attempts,
    )


@celery_app.task(name="dibs.monitor_watch", bind=True, max_retries=3)
def monitor_watch(self, watch_id: str) -> dict[str, object]:
    """Poll one watch and report what happened.

    The task reschedules itself through the service, so a successful run either
    finishes the watch or leaves exactly one successor job on the queue. A
    broker- or Redis-level failure is retried with backoff instead, which is
    the one case where the service never got to schedule the successor.
    """

    service = build_watch_service()
    try:
        result = asyncio.run(service.poll_once(watch_id))
    except Exception as exc:  # noqa: BLE001 - retry any infrastructure failure
        logger.exception("monitor_watch failed for %s", watch_id)
        raise self.retry(exc=exc, countdown=60) from exc

    return {
        "watch_id": watch_id,
        "outcome": result.outcome.value,
        "retry_in_seconds": result.retry_in_seconds,
    }
