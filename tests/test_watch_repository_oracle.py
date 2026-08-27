"""Differential oracle: the Redis-Lua repository must match the in-memory one.

Fixed-seed bounded operation traces are replayed against both stores under a
shared injected clock. After every operation the decision code and the
observable projection (status, revision, marker, terminal event) must be
identical. The simple in-memory store is the reference model; the exact Lua
implementation is checked against it. On any divergence the seed and the
operation trace are printed so the failure reproduces exactly.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import random

import fakeredis.aioredis as fakeredis_aio
import pytest

from backend.db.repositories.watch_decisions import ClaimStatus, CommitStatus
from backend.db.repositories.watches import (
    InMemoryWatchRepository,
    RedisWatchRepository,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import initial_runtime, window_id_for
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EXPIRES = datetime(2026, 9, 2, 5, 0, tzinfo=UTC)  # short: exhaustible by deadline
LEASE = 120.0
OWNERS = ("owner-a", "owner-b")


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _watch() -> Watch:
    return Watch(
        watch_id="watch_1",
        status=WatchStatus.ACTIVE,
        query=AvailabilityQuery(
            venue_name="Cote",
            venue_type=VenueType.RESTAURANT,
            market="Kitchener-Waterloo-Cambridge, ON",
            party_size=4,
            date="2026-09-05",
            preferred_time="19:00",
            time_window=None,
            duration_minutes=None,
            special_requests=[],
        ),
        created_at=NOW,
        updated_at=NOW,
        expires_at=EXPIRES,
        attempts=0,
        max_attempts=4,
        next_check_at=NOW,
    )


def _generate(rng: random.Random, length: int) -> list[tuple]:
    ops: list[tuple] = []
    for _ in range(length):
        kind = rng.choices(
            ["claim", "commit_miss", "commit_expire", "cancel", "expire", "advance"],
            weights=[4, 3, 1, 1, 1, 2],
        )[0]
        if kind in {"claim", "commit_miss", "commit_expire"}:
            ops.append((kind, rng.choice(OWNERS)))
        elif kind == "advance":
            ops.append((kind, rng.choice([30.0, 130.0, LEASE + 5.0])))
        else:
            ops.append((kind,))
    return ops


async def _run(repo, clock: Clock, ops: list[tuple]) -> list[tuple]:
    """Replay a trace, returning one comparable observation per operation."""

    await repo.create_with_schedule(
        _watch(), initial_runtime(_watch(), required_attempts=5, supports_deadline=False)
    )
    held: dict[str, object] = {}
    trace: list[tuple] = []

    for op in ops:
        note: tuple = ()
        if op[0] == "claim":
            owner = op[1]
            runtime = await repo.get_runtime("watch_1")
            # A real caller always claims a concrete window from a marker; when
            # the watch is terminal its runtime window is cleared, so fall back
            # to the initial id (the claim will report TERMINAL either way).
            window = (
                runtime.window_id
                if runtime and runtime.window_id
                else window_id_for("watch_1", 0)
            )
            result = await repo.claim_window("watch_1", window, owner, LEASE)
            if result.status is ClaimStatus.OWNED:
                held[owner] = result.claim
            note = (result.status.value,)
        elif op[0] in {"commit_miss", "commit_expire"}:
            owner = op[1]
            claim = held.get(owner)
            if claim is None:
                note = ("NO_CLAIM",)
            else:
                if op[0] == "commit_miss":
                    nxt = claim.runtime.cadence_sequence + 1
                    scheduled = clock() + timedelta(seconds=180)
                    new_watch = claim.watch.model_copy(
                        update={
                            "attempts": claim.watch.attempts + 1,
                            "updated_at": clock(),
                            "next_check_at": scheduled,
                        }
                    )
                    new_runtime = claim.runtime.model_copy(
                        update={
                            "cadence_sequence": nxt,
                            "window_id": window_id_for("watch_1", nxt),
                            "scheduled_for": scheduled,
                        }
                    )
                else:
                    new_watch = claim.watch.model_copy(
                        update={
                            "status": WatchStatus.EXPIRED,
                            "next_check_at": None,
                            "updated_at": clock(),
                        }
                    )
                    new_runtime = claim.runtime.model_copy(
                        update={"window_id": None, "scheduled_for": None}
                    )
                result = await repo.commit_window(claim, new_watch, new_runtime)
                if result.status is CommitStatus.COMMITTED:
                    held.pop(owner, None)
                note = (result.status.value, result.event_id)
        elif op[0] == "cancel":
            result = await repo.cancel_if_active("watch_1")
            note = (result.status.value,)
        elif op[0] == "expire":
            result = await repo.expire_if_eligible("watch_1")
            note = (result.status.value, result.event_id)
        elif op[0] == "advance":
            clock.advance(op[1])
            note = ("advanced",)

        watch = await repo.get("watch_1")
        runtime = await repo.get_runtime("watch_1")
        marker = await repo.schedule_marker("watch_1")
        projection = (
            watch.status.value if watch else None,
            runtime.revision if runtime else None,
            runtime.window_id if runtime else None,
            marker is not None,
        )
        trace.append((op, note, projection))

    return trace


@pytest.mark.parametrize("seed", range(12))
def test_redis_matches_memory_for_a_generated_trace(seed: int) -> None:
    ops = _generate(random.Random(seed), 30)

    async def scenario() -> tuple[list, list]:
        mem_clock = Clock(NOW)
        mem = await _run(InMemoryWatchRepository(clock=mem_clock), mem_clock, ops)
        redis_clock = Clock(NOW)
        client = fakeredis_aio.FakeRedis(decode_responses=True)
        red = await _run(
            RedisWatchRepository(client, clock=redis_clock), redis_clock, ops
        )
        await client.aclose()
        return mem, red

    memory_trace, redis_trace = asyncio.run(scenario())

    for index, (mem_step, redis_step) in enumerate(zip(memory_trace, redis_trace)):
        assert mem_step == redis_step, (
            f"seed={seed} diverged at step {index}\n"
            f"  op:     {ops[index]}\n"
            f"  memory: {mem_step}\n"
            f"  redis:  {redis_step}"
        )
