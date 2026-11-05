"""Execute the portable history SQL in a real SQL engine.

SQLite exercises the production predicates/upserts rather than duplicating
them in a fake. PostgreSQL driver and server behavior remain separate concerns.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fakeredis.aioredis import FakeRedis

from backend.db.repositories.watch_history import (
    _CLAIM_ANONYMOUS_SQL,
    _SELECT_FOR_OWNER_SQL,
    _SELECT_FOR_USER_SQL,
    WatchHistoryRepository,
)
from backend.db.repositories.watches import InMemoryWatchRepository, RedisWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import WatchStatus
from backend.services.watch_service import WatchService
from backend.workers.queue import RecordingTaskQueue


class SqlPool:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE watch_history (
            watch_id TEXT PRIMARY KEY, owner_client_id TEXT, user_id TEXT,
            status TEXT, created_at TEXT, updated_at TEXT, expires_at TEXT,
            watch_json TEXT, revision BIGINT NOT NULL DEFAULT -1
        )""")

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def run(self, query, args):
        params = {str(i): v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v
                  for i, v in enumerate(args, 1)}
        return self.db.execute(query, params)

    async def execute(self, query, *args):
        self.run(query, args)

    async def fetch(self, query, *args):
        return self.run(query, args).fetchall()

    async def close(self):
        self.db.close()


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def query():
    return AvailabilityQuery(
        venue_name="Cote", venue_type="RESTAURANT",
        market="Kitchener-Waterloo-Cambridge, ON", party_size=4,
        date="2026-09-05", preferred_time="19:00", time_window=None,
        duration_minutes=None, special_requests=[],
    )


@pytest.mark.parametrize("redis", [False, True])
def test_delayed_poll_cannot_overwrite_cancellation_in_sql(redis):
    async def scenario():
        pool = SqlPool()
        client = FakeRedis(decode_responses=True)
        live = RedisWatchRepository(client, clock=lambda: NOW) if redis else InMemoryWatchRepository(clock=lambda: NOW)
        entered, release = asyncio.Event(), asyncio.Event()
        class DelayedHistory(WatchHistoryRepository):
            async def record(self, watch, owner_client_id=None, user_id=None, *, revision=-1):
                if revision == 1:
                    entered.set()
                    await release.wait()
                await super().record(watch, owner_client_id, user_id, revision=revision)
        history = DelayedHistory(pool)
        service = WatchService(
            live, MockBookingAdapter(unavailable_venues={"Cote"}),
            RecordingTaskQueue(), history=history, clock=lambda: NOW,
        )
        try:
            watch = await service.create(query())
            poll = asyncio.create_task(service.poll_once(watch.watch_id))
            await asyncio.wait_for(entered.wait(), 2)
            await service.cancel(watch.watch_id)
            release.set()
            await poll
            projected = await history.get(watch.watch_id)
            assert projected.status is WatchStatus.CANCELLED
            assert (await live.get(watch.watch_id)).status is WatchStatus.CANCELLED
            assert pool.db.execute("SELECT revision FROM watch_history").fetchone()[0] == 2
        finally:
            release.set()
            await pool.close()
            await client.aclose()
    asyncio.run(scenario())


def test_sql_orders_active_updates_by_revision_even_with_clock_skew():
    async def scenario():
        pool = SqlPool()
        history = WatchHistoryRepository(pool)
        service = WatchService(InMemoryWatchRepository(), MockBookingAdapter(), RecordingTaskQueue(), clock=lambda: NOW)
        watch = await service.create(query())
        try:
            await history.record(watch, revision=2)
            stale = watch.model_copy(update={"updated_at": NOW + timedelta(hours=1)})
            await history.record(stale, revision=1)
            assert await history.get(watch.watch_id) == watch
            newer = watch.model_copy(update={"updated_at": NOW - timedelta(hours=1), "attempts": 1})
            await history.record(newer, revision=3)
            assert await history.get(watch.watch_id) == newer
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_account_creation_and_claim_hide_watches_from_anonymous_sql_listing() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("""CREATE TABLE watch_history (
            watch_id TEXT, owner_client_id TEXT, user_id TEXT,
            updated_at TEXT, watch_json TEXT
        )""")
        db.executemany(
            "INSERT INTO watch_history VALUES (?, 'browser', ?, '2026-11-05', ?)",
            [("account-watch", "account-a", "account-watch"),
             ("anonymous-watch", None, "anonymous-watch")],
        )
        anonymous = {"1": "browser", "2": 100}
        assert db.execute(_SELECT_FOR_OWNER_SQL, anonymous).fetchall() == [("anonymous-watch",)]
        db.execute(_CLAIM_ANONYMOUS_SQL, {"1": "browser", "2": "account-a"})
        assert db.execute(_SELECT_FOR_OWNER_SQL, anonymous).fetchall() == []
        account = db.execute(_SELECT_FOR_USER_SQL, {"1": "account-a", "2": 100})
        assert {r[0] for r in account} == {"account-watch", "anonymous-watch"}
