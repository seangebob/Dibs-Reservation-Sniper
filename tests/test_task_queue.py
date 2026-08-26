"""The dispatch boundary between the API and the background workers."""

import asyncio

from backend.workers.queue import AsyncioTaskQueue, CeleryTaskQueue, RecordingTaskQueue


def test_recording_queue_captures_dispatches() -> None:
    queue = RecordingTaskQueue()

    asyncio.run(queue.enqueue_watch_poll("watch_1", delay_seconds=42.0))

    assert queue.dispatches == [("watch_1", 42.0)]


def test_asyncio_queue_runs_the_poll_after_the_delay() -> None:
    polled: list[str] = []

    async def scenario() -> None:
        queue = AsyncioTaskQueue(lambda watch_id: _record(polled, watch_id))
        await queue.enqueue_watch_poll("watch_1", delay_seconds=0.01)
        assert polled == []  # not yet: the delay has to elapse first
        await asyncio.sleep(0.05)
        await queue.close()

    asyncio.run(scenario())

    assert polled == ["watch_1"]


def test_asyncio_queue_survives_a_failing_poll() -> None:
    """One broken watch must not take down the others."""

    polled: list[str] = []

    async def poll(watch_id: str) -> None:
        if watch_id == "watch_bad":
            raise RuntimeError("boom")
        polled.append(watch_id)

    async def scenario() -> None:
        queue = AsyncioTaskQueue(poll)
        await queue.enqueue_watch_poll("watch_bad")
        await queue.enqueue_watch_poll("watch_good")
        await asyncio.sleep(0.05)
        await queue.close()

    asyncio.run(scenario())

    assert polled == ["watch_good"]


def test_closing_the_queue_cancels_pending_polls() -> None:
    polled: list[str] = []

    async def scenario() -> None:
        queue = AsyncioTaskQueue(lambda watch_id: _record(polled, watch_id))
        await queue.enqueue_watch_poll("watch_1", delay_seconds=30.0)
        await queue.close()
        # A dispatch after close is dropped rather than raising.
        await queue.enqueue_watch_poll("watch_2")
        await asyncio.sleep(0.02)

    asyncio.run(scenario())

    assert polled == []


class FakeCeleryTask:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_async(self, *, args: list[str], countdown: float) -> None:
        self.calls.append({"args": args, "countdown": countdown})


def test_celery_queue_passes_the_delay_as_a_countdown() -> None:
    task = FakeCeleryTask()

    asyncio.run(CeleryTaskQueue(task).enqueue_watch_poll("watch_1", delay_seconds=175.5))

    assert task.calls == [{"args": ["watch_1"], "countdown": 175.5}]


def test_celery_queue_never_sends_a_negative_countdown() -> None:
    task = FakeCeleryTask()

    asyncio.run(CeleryTaskQueue(task).enqueue_watch_poll("watch_1", delay_seconds=-5.0))

    assert task.calls[0]["countdown"] == 0.0


async def _record(sink: list[str], watch_id: str) -> None:
    sink.append(watch_id)
