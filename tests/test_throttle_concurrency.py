"""Concurrent API requests must reserve quota before doing paid/auth work."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fakeredis.aioredis import FakeRedis

from backend.main import create_app, get_auth_service
from backend.models.account import User
from backend.services.auth_service import InvalidCredentialsError
from backend.services.throttle import (
    RedisSlidingWindowThrottle, SlidingWindowThrottle, TooManyLoginAttemptsError,
)


@pytest.mark.parametrize("redis", [False, True])
def test_pending_login_attempts_count_and_success_releases_only_its_ip_slot(redis):
    async def scenario():
        client = FakeRedis()
        app = create_app()
        def throttle(name):
            options = dict(max_events=3, window_seconds=60, on_limit=TooManyLoginAttemptsError)
            return RedisSlidingWindowThrottle(client, name=name, **options) if redis else SlidingWindowThrottle(**options)
        app.state.login_throttle = throttle("email")
        app.state.login_ip_throttle = throttle("ip")
        calls = []
        succeeds = False
        class Auth:
            async def login(self, email, password):
                calls.append(email)
                await asyncio.sleep(0.01)
                if succeeds:
                    return User(id=uuid4(), email=email, created_at=datetime.now(UTC)), "token"
                raise InvalidCredentialsError()
        app.dependency_overrides[get_auth_service] = lambda: Auth()
        transport = httpx.ASGITransport(app=app, client=("8.8.8.8", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
            responses = await asyncio.gather(*(
                api.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong"})
                for _ in range(10)
            ))
            assert sorted(r.status_code for r in responses) == [401] * 3 + [429] * 7
            assert len(calls) == 3
            await app.state.login_throttle.areset("email:a@example.com")
            await app.state.login_ip_throttle.areset("ip:8.8.8.8")
            succeeds = True
            for _ in range(5):
                response = await api.post("/api/auth/login", json={"email": "a@example.com", "password": "correct"})
                assert response.status_code == 200
            # Success did not occupy IP quota or leave pending reservations.
            succeeds = False
            for _ in range(3):
                response = await api.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong"})
                assert response.status_code == 401
        await client.aclose()
    asyncio.run(scenario())
