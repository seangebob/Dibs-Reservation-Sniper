"""Celery application bound to the Redis broker.

Run a worker with:

    celery -A backend.workers.celery_app worker --loglevel=info

Celery is an optional dependency (`pip install -e ".[worker]"`). Importing this
module without it installed raises a clear error rather than a bare
ImportError, because the API itself never needs it: it dispatches through the
`TaskQueue` protocol in `backend.workers.queue`.
"""

import os

from backend.config import DEFAULT_REDIS_URL


try:
    from celery import Celery
except ModuleNotFoundError as exc:  # pragma: no cover - depends on the install
    raise ModuleNotFoundError(
        "Celery is not installed. Install the worker extra with "
        '`pip install -e ".[worker]"` to run background jobs.'
    ) from exc


broker_url = os.getenv("REDIS_URL", DEFAULT_REDIS_URL)

celery_app = Celery(
    "dibs",
    broker=broker_url,
    backend=broker_url,
    include=["backend.workers.tasks.monitor_watch"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.getenv("RESERVATION_TIMEZONE", "America/Toronto"),
    enable_utc=True,
    # A poll is a network call to one provider; it should never sit for
    # minutes, and a stuck one must not pin a worker slot forever.
    task_soft_time_limit=60,
    task_time_limit=90,
    # Watches reschedule themselves, so a lost prefetch is worse than a
    # duplicate: acknowledge only once the poll has actually finished.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
