"""Dispatch boundary between the API and the background workers.

The API only ever needs to say "poll this watch in N seconds". Keeping that
behind a small protocol means the request path never imports Celery, tests can
assert on dispatches without a broker, and local development can run the queue
in-process before Redis and a worker are up.
"""

import asyncio
import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class TaskQueue(Protocol):
    """Somewhere to hand a delayed watch poll."""

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        """Schedule one availability check for a watch."""
        ...

    async def close(self) -> None:
        """Release queue-owned resources."""
        ...


class RecordingTaskQueue:
    """Records dispatches instead of running them. Used by tests."""

    def __init__(self) -> None:
        self.dispatches: list[tuple[str, float]] = []

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.dispatches.append((watch_id, delay_seconds))

    async def close(self) -> None:
        return None


class AsyncioTaskQueue:
    """In-process queue backed by asyncio tasks.

    This is the development default: it gives a real event-driven loop with
    real jittered delays without needing a broker or a separate worker
    process. It is deliberately not durable -- pending polls are lost on
    restart -- so `CeleryTaskQueue` is what should run anywhere that matters.
    """

    def __init__(self, poll: Any) -> None:
        self._poll = poll
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        if self._closed:
            logger.warning("Dropping poll for watch %s: queue is closed", watch_id)
            return

        task = asyncio.create_task(self._run_later(watch_id, delay_seconds))
        # A task referenced only by the event loop can be garbage collected
        # mid-flight, so the queue holds a strong reference until it finishes.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_later(self, watch_id: str, delay_seconds: float) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await self._poll(watch_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One failing watch must not take down the others; the service
            # records the error on the watch itself.
            logger.exception("Background poll failed for watch %s", watch_id)

    async def close(self) -> None:
        self._closed = True
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class CeleryTaskQueue:
    """Hands polls to a Celery worker over the Redis broker.

    Celery's own API is synchronous, and `apply_async` only talks to the
    broker, so it is cheap enough to call directly from the event loop.
    """

    def __init__(self, task: Any) -> None:
        self._task = task

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._task.apply_async(
            args=[watch_id],
            countdown=max(0.0, delay_seconds),
        )

    async def close(self) -> None:
        return None
