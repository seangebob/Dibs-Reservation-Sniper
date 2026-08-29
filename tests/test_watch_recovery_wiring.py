"""Recovery coordinator wiring in `main.lifespan` (spec task 10.2).

`_attach_redis` selects the final repository/queue/mock state; these tests
confirm the recovery coordinator is built over exactly those final components,
that an unsupported Redis Cluster topology is refused the same way an
unreachable server is, and that shutdown releases leadership and cancels the
follow-up sweep in order, idempotently.
"""

import asyncio
from typing import Any

from fastapi.testclient import TestClient
import pytest

import fakeredis.aioredis as fakeredis_aio

from backend.db import database
from backend.db.repositories.watches import (
    LEADER_KEY,
    InMemoryWatchRepository,
    RedisWatchRepository,
)
from backend.main import create_app
from backend.services.watch_recovery import RecoveryCoordinator
from backend.workers.dispatcher import WatchScheduleDispatcher


def _prepare_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "RESERVATION_TIMEZONE",
        "REDIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-recovery-wiring")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")


class _ClusterRedisClient:
    """A reachable server that reports Redis Cluster mode."""

    def __init__(self) -> None:
        self.ping_calls = 0
        self.info_calls = 0
        self.close_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        return True

    async def info(self) -> dict[str, Any]:
        self.info_calls += 1
        return {"cluster_enabled": 1}

    async def aclose(self) -> None:
        self.close_calls += 1


def test_redis_cluster_mode_refuses_the_upgrade_and_stays_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_environment(monkeypatch)
    redis_client = _ClusterRedisClient()
    monkeypatch.setattr(database, "create_redis_client", lambda _url: redis_client)

    fresh = create_app()
    with TestClient(fresh) as client:
        assert fresh.state.redis is None
        assert isinstance(fresh.state.watch_repository, InMemoryWatchRepository)
        assert redis_client.ping_calls == 1
        assert redis_client.info_calls == 1
        assert redis_client.close_calls == 1

        coordinator = fresh.state.recovery_coordinator
        assert isinstance(coordinator, RecoveryCoordinator)
        assert coordinator._distributed is False
        assert coordinator._repository is fresh.state.watch_repository
        assert fresh.state.recovery_sweep_task is not None

        assert client.get("/health").json()["watch_store"] == "memory"

    # Shutdown is idempotent even though a non-distributed coordinator never
    # touched a leader lease.
    assert fresh.state.recovery_coordinator is None
    assert fresh.state.recovery_sweep_task is None


def test_the_recovery_coordinator_reconciles_over_the_final_redis_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_environment(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://recovery-wiring.example.test:6379/0")
    fake_client = fakeredis_aio.FakeRedis(decode_responses=True)
    monkeypatch.setattr(database, "create_redis_client", lambda _url: fake_client)

    fresh = create_app()
    with TestClient(fresh) as client:
        assert fresh.state.redis is fake_client
        assert isinstance(fresh.state.watch_repository, RedisWatchRepository)

        coordinator = fresh.state.recovery_coordinator
        assert isinstance(coordinator, RecoveryCoordinator)
        assert coordinator._distributed is True
        assert coordinator._repository is fresh.state.watch_repository
        assert coordinator._owner_id == fresh.state.recovery_owner_id

        # The initial reconciliation pass ran during startup and won the
        # leader lease (no other replica is contending for it here).
        assert client.get("/health").json()["watch_store"] == "redis"

    # Shutdown released the leader lease and stopped the follow-up sweep.
    assert fresh.state.recovery_coordinator is None
    assert fresh.state.recovery_sweep_task is None


def test_shutdown_releases_the_redis_leader_lease() -> None:
    async def scenario() -> None:
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        repo = RedisWatchRepository(client)
        dispatcher = WatchScheduleDispatcher(
            repo, _NullQueue(), owner_id="owner-a", horizon_seconds=300.0
        )
        coordinator = RecoveryCoordinator(
            repo,
            dispatcher,
            owner_id="owner-a",
            distributed=True,
            leader_lease_seconds=30.0,
            earliest_delay_seconds=150.0,
        )

        await coordinator.reconcile_once()
        assert await client.get(LEADER_KEY) == "owner-a"

        await coordinator.release()
        assert await client.get(LEADER_KEY) is None

    asyncio.run(scenario())


class _NullQueue:
    async def enqueue_watch_poll(self, *args: object, **kwargs: object) -> None:
        pass
