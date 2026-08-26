"""Watch persistence: an interface, an in-memory store, and a Redis store."""

import asyncio
from typing import Any, Protocol

from pydantic import ValidationError

from backend.models.watch import Watch, WatchStatus


#: Every key this module owns lives under one prefix so a shared Redis
#: instance (the Celery broker uses the same server) stays legible.
KEY_PREFIX = "dibs:watch"
INDEX_KEY = "dibs:watches"
ACTIVE_INDEX_KEY = "dibs:watches:active"


class WatchRepository(Protocol):
    """Storage boundary for watches, implemented in memory and on Redis."""

    async def save(self, watch: Watch) -> Watch:
        """Insert or replace one watch."""
        ...

    async def get(self, watch_id: str) -> Watch | None:
        """Return one watch, or None when it is unknown."""
        ...

    async def list_all(self) -> list[Watch]:
        """Return every stored watch, newest first."""
        ...

    async def list_active(self) -> list[Watch]:
        """Return only watches the queue should still poll."""
        ...

    async def delete(self, watch_id: str) -> bool:
        """Remove one watch, returning whether it existed."""
        ...


class InMemoryWatchRepository:
    """Process-local store used by tests and by `--no-redis` local runs."""

    def __init__(self) -> None:
        self._watches: dict[str, Watch] = {}
        self._lock = asyncio.Lock()

    async def save(self, watch: Watch) -> Watch:
        async with self._lock:
            self._watches[watch.watch_id] = watch
            return watch

    async def get(self, watch_id: str) -> Watch | None:
        async with self._lock:
            return self._watches.get(watch_id)

    async def list_all(self) -> list[Watch]:
        async with self._lock:
            watches = list(self._watches.values())
        return sorted(watches, key=lambda watch: watch.created_at, reverse=True)

    async def list_active(self) -> list[Watch]:
        return [
            watch
            for watch in await self.list_all()
            if watch.status is WatchStatus.ACTIVE
        ]

    async def delete(self, watch_id: str) -> bool:
        async with self._lock:
            return self._watches.pop(watch_id, None) is not None


class RedisWatchRepository:
    """Redis-backed store keeping one JSON document per watch.

    The client is injected rather than constructed here so the same repository
    works against a real server, a test double, or a future connection pool.
    Two sets index the documents: every watch, and the active subset the
    scheduler sweeps on startup.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def save(self, watch: Watch) -> Watch:
        pipeline = self._client.pipeline()
        pipeline.set(self._key(watch.watch_id), watch.model_dump_json())
        pipeline.sadd(INDEX_KEY, watch.watch_id)
        if watch.status is WatchStatus.ACTIVE:
            pipeline.sadd(ACTIVE_INDEX_KEY, watch.watch_id)
        else:
            pipeline.srem(ACTIVE_INDEX_KEY, watch.watch_id)
        await pipeline.execute()
        return watch

    async def get(self, watch_id: str) -> Watch | None:
        raw = await self._client.get(self._key(watch_id))
        return self._decode(watch_id, raw)

    async def list_all(self) -> list[Watch]:
        watch_ids = await self._client.smembers(INDEX_KEY)
        return await self._load_many(watch_ids)

    async def list_active(self) -> list[Watch]:
        watch_ids = await self._client.smembers(ACTIVE_INDEX_KEY)
        watches = await self._load_many(watch_ids)
        # The index can lag behind a document that was updated elsewhere, so
        # status on the document itself is the authority.
        return [watch for watch in watches if watch.status is WatchStatus.ACTIVE]

    async def delete(self, watch_id: str) -> bool:
        pipeline = self._client.pipeline()
        pipeline.delete(self._key(watch_id))
        pipeline.srem(INDEX_KEY, watch_id)
        pipeline.srem(ACTIVE_INDEX_KEY, watch_id)
        results = await pipeline.execute()
        return bool(results[0])

    async def _load_many(self, watch_ids: Any) -> list[Watch]:
        ordered_ids = sorted(watch_ids or ())
        if not ordered_ids:
            return []

        raw_documents = await self._client.mget(
            [self._key(watch_id) for watch_id in ordered_ids]
        )
        watches = [
            watch
            for watch_id, raw in zip(ordered_ids, raw_documents, strict=True)
            if (watch := self._decode(watch_id, raw)) is not None
        ]
        return sorted(watches, key=lambda watch: watch.created_at, reverse=True)

    @staticmethod
    def _decode(watch_id: str, raw: Any) -> Watch | None:
        """Parse a stored document, treating corrupt JSON as a missing watch.

        A document written by an older schema must not take down the whole
        listing, and there is nothing useful a caller could do with it.
        """

        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return Watch.model_validate_json(raw)
        except ValidationError:
            return None

    @staticmethod
    def _key(watch_id: str) -> str:
        return f"{KEY_PREFIX}:{watch_id}"
