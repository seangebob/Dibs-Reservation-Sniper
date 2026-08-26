"""Redis connection factory.

Milestone 3 keeps watch state in Redis, which is already required as the
Celery broker. PostgreSQL arrives in Milestone 4 for durable user-owned
records; the repository interface in `db.repositories.watches` is what makes
that swap a one-line wiring change.
"""

from typing import Any

from redis.asyncio import Redis


def create_redis_client(redis_url: str) -> Redis:
    """Return an asyncio Redis client that decodes values to `str`."""

    return Redis.from_url(redis_url, decode_responses=True)


async def ping(client: Any) -> bool:
    """Return whether Redis is reachable, without raising on failure."""

    try:
        await client.ping()
    except Exception:  # noqa: BLE001 - health checks must not raise
        return False
    return True
