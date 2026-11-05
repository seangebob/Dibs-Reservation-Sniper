"""Redis startup logs must not disclose connection credentials."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis

import backend.main as main
from backend.config import WatchSettings


@pytest.mark.parametrize("mode", ["unreachable", "cluster", "ready"])
def test_redis_startup_redacts_credentials_in_every_mode(monkeypatch, caplog, mode):
    async def scenario():
        client = FakeRedis(decode_responses=True)
        app = main.create_app()
        app.state.watch_settings = WatchSettings(
            redis_url="redis://private-user:secret-password@localhost:6379/0?token=secret-token"
        )
        monkeypatch.setattr("backend.db.database.create_redis_client", lambda _: client)
        monkeypatch.setattr("backend.db.database.ping", AsyncMock(return_value=mode != "unreachable"))
        monkeypatch.setattr(main, "_redis_cluster_enabled", AsyncMock(return_value=mode == "cluster"))
        monkeypatch.setattr(main, "_start_recovery", AsyncMock())
        try:
            await main._attach_redis(app)
        finally:
            await app.state.watch_queue.close()
            await client.aclose()

    with caplog.at_level(logging.INFO, logger="backend.main"):
        asyncio.run(scenario())
    assert "redis://localhost:6379/0" in caplog.text
    for secret in ("private-user", "secret-password", "secret-token"):
        assert secret not in caplog.text
