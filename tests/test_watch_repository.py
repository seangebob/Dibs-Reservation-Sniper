"""Watch persistence, exercised against both repository implementations."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.db.repositories.watches import (
    ACTIVE_INDEX_KEY,
    INDEX_KEY,
    InMemoryWatchRepository,
    RedisWatchRepository,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


class FakeRedis:
    """Minimal async stand-in for the Redis commands the repository uses."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.strings.get(key) for key in keys]

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def pipeline(self) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._commands: list[tuple[str, tuple[object, ...]]] = []

    def set(self, key: str, value: str) -> None:
        self._commands.append(("set", (key, value)))

    def sadd(self, key: str, member: str) -> None:
        self._commands.append(("sadd", (key, member)))

    def srem(self, key: str, member: str) -> None:
        self._commands.append(("srem", (key, member)))

    def delete(self, key: str) -> None:
        self._commands.append(("delete", (key,)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for name, args in self._commands:
            if name == "set":
                self._client.strings[args[0]] = args[1]
                results.append(True)
            elif name == "sadd":
                self._client.sets.setdefault(args[0], set()).add(args[1])
                results.append(1)
            elif name == "srem":
                self._client.sets.setdefault(args[0], set()).discard(args[1])
                results.append(1)
            elif name == "delete":
                results.append(int(self._client.strings.pop(args[0], None) is not None))
        self._commands.clear()
        return results


def query(venue_name: str = "Cote") -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name=venue_name,
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def watch(
    watch_id: str = "watch_1",
    *,
    status: WatchStatus = WatchStatus.ACTIVE,
    created_at: datetime = NOW,
) -> Watch:
    return Watch(
        watch_id=watch_id,
        status=status,
        query=query(),
        auto_book=False,
        created_at=created_at,
        updated_at=created_at,
        expires_at=created_at + timedelta(days=2),
        attempts=0,
        max_attempts=10,
        next_check_at=created_at if status is WatchStatus.ACTIVE else None,
    )


@pytest.fixture(params=["memory", "redis"])
def repository(request: pytest.FixtureRequest):
    """Both stores must satisfy the same contract."""

    if request.param == "memory":
        return InMemoryWatchRepository()
    return RedisWatchRepository(FakeRedis())


def test_saved_watch_round_trips(repository) -> None:
    original = watch()

    asyncio.run(repository.save(original))
    stored = asyncio.run(repository.get("watch_1"))

    assert stored == original


def test_unknown_watch_is_none(repository) -> None:
    assert asyncio.run(repository.get("watch_missing")) is None


def test_listing_is_newest_first(repository) -> None:
    asyncio.run(repository.save(watch("watch_old", created_at=NOW)))
    asyncio.run(
        repository.save(watch("watch_new", created_at=NOW + timedelta(hours=1)))
    )

    listed = asyncio.run(repository.list_all())

    assert [record.watch_id for record in listed] == ["watch_new", "watch_old"]


def test_only_active_watches_are_listed_as_active(repository) -> None:
    asyncio.run(repository.save(watch("watch_live")))
    asyncio.run(repository.save(watch("watch_done", status=WatchStatus.CANCELLED)))

    active = asyncio.run(repository.list_active())

    assert [record.watch_id for record in active] == ["watch_live"]


def test_finishing_a_watch_removes_it_from_the_active_set(repository) -> None:
    asyncio.run(repository.save(watch("watch_1")))
    asyncio.run(repository.save(watch("watch_1", status=WatchStatus.CANCELLED)))

    assert asyncio.run(repository.list_active()) == []
    assert len(asyncio.run(repository.list_all())) == 1


def test_delete_reports_whether_the_watch_existed(repository) -> None:
    asyncio.run(repository.save(watch("watch_1")))

    assert asyncio.run(repository.delete("watch_1")) is True
    assert asyncio.run(repository.delete("watch_1")) is False
    assert asyncio.run(repository.get("watch_1")) is None


def test_redis_keys_stay_under_one_namespace() -> None:
    client = FakeRedis()
    repository = RedisWatchRepository(client)

    asyncio.run(repository.save(watch("watch_1")))

    assert list(client.strings) == ["dibs:watch:watch_1"]
    assert client.sets[INDEX_KEY] == {"watch_1"}
    assert client.sets[ACTIVE_INDEX_KEY] == {"watch_1"}


def test_corrupt_document_is_skipped_rather_than_crashing_the_listing() -> None:
    client = FakeRedis()
    repository = RedisWatchRepository(client)
    asyncio.run(repository.save(watch("watch_good")))
    client.strings["dibs:watch:watch_bad"] = '{"not":"a watch"}'
    client.sets[INDEX_KEY].add("watch_bad")

    listed = asyncio.run(repository.list_all())

    assert [record.watch_id for record in listed] == ["watch_good"]
    assert asyncio.run(repository.get("watch_bad")) is None
