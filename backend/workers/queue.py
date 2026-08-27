"""Dispatch boundary between the API and the background workers.

The API only ever needs to say "poll this watch in N seconds". Keeping that
behind a small protocol means the request path never imports Celery, tests can
assert on dispatches without a broker, and local development can run the queue
in-process before Redis and a worker are up.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)

#: Longest single sleep before the queue re-reads the clock. A process that was
#: suspended past its due time then wakes recomputes the remainder rather than
#: oversleeping by the suspended duration.
_MAX_SLEEP_SECONDS = 60.0


class TaskQueue(Protocol):
    """Somewhere to hand a delayed watch poll.

    `window_id` identifies the one logical cadence window a poll belongs to;
    redeliveries of the same window share it. It is optional so pre-milestone-3
    callers and already-queued one-argument jobs keep working.
    """

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        window_id: str | None = None,
        delay_seconds: float = 0.0,
        due_at: datetime | None = None,
        task_id: str | None = None,
    ) -> None:
        """Schedule one availability check for a watch."""
        ...

    async def close(self) -> None:
        """Release queue-owned resources."""
        ...


class RecordingTaskQueue:
    """Records dispatches instead of running them. Used by tests."""

    def __init__(self) -> None:
        #: Legacy 2-tuples, kept so existing assertions are unaffected.
        self.dispatches: list[tuple[str, float]] = []
        #: Window-aware detail for milestone-3 tests.
        self.window_dispatches: list[tuple[str, str | None, float]] = []

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        window_id: str | None = None,
        delay_seconds: float = 0.0,
        due_at: datetime | None = None,
        task_id: str | None = None,
    ) -> None:
        self.dispatches.append((watch_id, delay_seconds))
        self.window_dispatches.append((watch_id, window_id, delay_seconds))

    async def close(self) -> None:
        return None


class AsyncioTaskQueue:
    """In-process queue backed by asyncio tasks.

    This is the development default: it gives a real event-driven loop with
    real jittered delays without needing a broker or a separate worker
    process. It is deliberately not durable -- pending polls are lost on
    restart -- so `CeleryTaskQueue` is what should run anywhere that matters.
    """

    def __init__(
        self,
        poll: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._poll = poll
        self._tasks: set[asyncio.Task[None]] = set()
        #: One live task per cadence window, so a duplicate delivery in this
        #: process reuses the existing task instead of scheduling a second.
        self._by_window: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self._clock = clock or (lambda: datetime.now(UTC))

    def is_ready(self) -> bool:
        """Whether the queue is open and can still accept work."""

        return not self._closed

    async def enqueue_watch_poll(
        self,
        watch_id: str,
        *,
        window_id: str | None = None,
        delay_seconds: float = 0.0,
        due_at: datetime | None = None,
        task_id: str | None = None,
    ) -> None:
        if self._closed:
            logger.warning("Dropping poll for watch %s: queue is closed", watch_id)
            return

        if due_at is None:
            due_at = self._clock() + timedelta(seconds=max(0.0, delay_seconds))

        if window_id is None:
            task = asyncio.create_task(self._run_at(watch_id, None, due_at))
            # A task referenced only by the event loop can be garbage collected
            # mid-flight, so the queue holds a strong reference until it ends.
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        existing = self._by_window.get(window_id)
        if existing is not None and not existing.done():
            return  # dedup: this window is already scheduled in this process
        task = asyncio.create_task(self._run_at(watch_id, window_id, due_at))
        self._by_window[window_id] = task
        task.add_done_callback(self._forget_window_for(window_id))

    def _forget_window_for(
        self,
        window_id: str,
    ) -> Callable[[asyncio.Task[None]], None]:
        def forget(task: asyncio.Task[None]) -> None:
            if self._by_window.get(window_id) is task:
                del self._by_window[window_id]

        return forget

    async def _run_at(
        self,
        watch_id: str,
        window_id: str | None,
        due_at: datetime,
    ) -> None:
        try:
            while True:
                remaining = (due_at - self._clock()).total_seconds()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, _MAX_SLEEP_SECONDS))
            await self._poll(watch_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One failing watch must not take down the others; the service
            # records the error on the watch itself.
            logger.exception("Background poll failed for watch %s", watch_id)

    async def close(self) -> None:
        self._closed = True
        pending = list(self._tasks) + list(self._by_window.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._by_window.clear()


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
        window_id: str | None = None,
        delay_seconds: float = 0.0,
        due_at: datetime | None = None,
        task_id: str | None = None,
    ) -> None:
        # New jobs carry the window so the worker resolves the same logical
        # cadence; old one-argument messages remain valid.
        args = [watch_id] if window_id is None else [watch_id, window_id]
        kwargs: dict[str, Any] = {
            "args": args,
            "countdown": max(0.0, delay_seconds),
        }
        if task_id is not None:
            kwargs["task_id"] = task_id
        self._task.apply_async(**kwargs)

    async def close(self) -> None:
        return None
