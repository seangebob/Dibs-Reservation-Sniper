"""The Redis-backed throttle shares one window across API processes."""

from __future__ import annotations

import asyncio

from fakeredis import aioredis as fakeredis_aio
import pytest

from backend.main import _share_throttles_through_redis, create_app
from backend.services.throttle import (
    RateLimitedError,
    RedisSlidingWindowThrottle,
    SlidingWindowThrottle,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _throttle(client, clock, *, max_events: int = 3, window: int = 300):
    return RedisSlidingWindowThrottle(
        client,
        name="test",
        max_events=max_events,
        window_seconds=window,
        clock=clock,
    )


def test_two_processes_share_one_limit() -> None:
    async def scenario() -> None:
        client = fakeredis_aio.FakeRedis()
        clock = _Clock()
        first, second = _throttle(client, clock), _throttle(client, clock)

        await first.arecord("k")
        await second.arecord("k")
        await first.arecord("k")

        with pytest.raises(RateLimitedError):
            await second.acheck("k")
        await second.acheck("other-key")
        await client.aclose()

    asyncio.run(scenario())


def test_events_age_out_of_the_window() -> None:
    async def scenario() -> None:
        client = fakeredis_aio.FakeRedis()
        clock = _Clock()
        throttle = _throttle(client, clock, window=300)
        for _ in range(3):
            await throttle.arecord("k")

        clock.now += 301
        await throttle.acheck("k")
        await client.aclose()

    asyncio.run(scenario())


def test_reset_clears_the_window() -> None:
    async def scenario() -> None:
        client = fakeredis_aio.FakeRedis()
        throttle = _throttle(client, _Clock())
        for _ in range(3):
            await throttle.arecord("k")

        await throttle.areset("k")
        await throttle.acheck("k")
        await client.aclose()

    asyncio.run(scenario())


def test_a_redis_outage_fails_open() -> None:
    class BrokenRedis:
        def pipeline(self, transaction: bool = True):
            raise ConnectionError("redis is down")

        async def delete(self, key: str) -> None:
            raise ConnectionError("redis is down")

    throttle = _throttle(BrokenRedis(), _Clock(), max_events=1)

    async def scenario() -> None:
        await throttle.arecord("k")
        await throttle.acheck("k")
        await throttle.areset("k")

    asyncio.run(scenario())


def test_startup_moves_every_throttle_into_redis_with_the_same_limit() -> None:
    app = create_app()
    app.state.login_throttle = SlidingWindowThrottle(max_events=7, window_seconds=60)
    app.state.login_ip_throttle = SlidingWindowThrottle(
        max_events=9, window_seconds=60
    )
    client = fakeredis_aio.FakeRedis()

    _share_throttles_through_redis(app, client)

    for attr, limit in (("login_throttle", 7), ("login_ip_throttle", 9)):
        shared = getattr(app.state, attr)
        assert isinstance(shared, RedisSlidingWindowThrottle)
        assert shared._max_events == limit
    assert isinstance(app.state.prompt_throttle, RedisSlidingWindowThrottle)
