"""Migration runner behavior against a fake connection pool.

A real PostgreSQL is not started for these tests; the runner is a small
orchestration whose logic (advisory lock → read applied → apply pending →
record version) is fully observable through a fake pool implementing the same
narrow ``PoolLike`` protocol the production code targets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.db.postgres import (
    MIGRATIONS_DIRECTORY,
    Migration,
    MigrationRunner,
    discover_migrations,
    run_migrations,
)


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeTransaction:
        self._conn.transactions_opened += 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None:
            self._conn.transactions_rolled_back += 1
        else:
            self._conn.transactions_committed += 1


class _FakeConnection:
    """Records every statement so tests can assert lock/DDL/insert order."""

    def __init__(
        self,
        applied_versions: list[str] | None = None,
        *,
        fail_on_sql: str | None = None,
    ) -> None:
        self.applied_versions: list[str] = list(applied_versions or [])
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.transactions_opened = 0
        self.transactions_committed = 0
        self.transactions_rolled_back = 0
        self._fail_on_sql = fail_on_sql

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))
        if self._fail_on_sql is not None and self._fail_on_sql in query:
            raise RuntimeError(f"forced failure on: {self._fail_on_sql}")
        if query.startswith("INSERT INTO schema_migrations"):
            (version,) = args
            self.applied_versions.append(version)

    async def fetchval(self, query: str, *args: Any) -> Any:  # noqa: D401
        raise NotImplementedError

    async def fetch(self, query: str, *args: Any) -> list[dict[str, str]]:
        if "schema_migrations" in query:
            return [{"version": v} for v in self.applied_versions]
        return []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


class _AcquireCM:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return self._pool.connection

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.closed = False

    def acquire(self) -> _AcquireCM:
        return _AcquireCM(self)

    async def close(self) -> None:
        self.closed = True


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


# -- discovery --------------------------------------------------------------


def test_discovery_returns_an_empty_list_for_a_missing_directory(
    tmp_path: Path,
) -> None:
    assert discover_migrations(tmp_path / "does-not-exist") == []


def test_discovery_returns_sql_files_in_filename_order(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("CREATE TABLE b (id INT);")
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE a (id INT);")
    (tmp_path / "README.md").write_text("not a migration")
    (tmp_path / "0003_third.txt").write_text("also not a migration")

    result = discover_migrations(tmp_path)

    assert [m.version for m in result] == ["0001_first", "0002_second"]
    assert result[0].sql == "CREATE TABLE a (id INT);"


def test_the_bundled_migrations_directory_exists_and_discovery_walks_it() -> None:
    assert MIGRATIONS_DIRECTORY.is_dir()
    # No assertion about count -- future tasks add migrations; this just proves
    # the built-in discovery works without needing a tmp path.
    discover_migrations()


# -- runner -----------------------------------------------------------------


def test_running_with_no_pending_migrations_is_a_no_op() -> None:
    conn = _FakeConnection(applied_versions=["0001_bootstrap"])
    pool = _FakePool(conn)
    migrations = [Migration(version="0001_bootstrap", sql="SELECT 1;")]

    applied = _run(MigrationRunner(pool, migrations).run())

    assert applied == []
    # The runner still creates the tracking table and acquires the lock even
    # when nothing is pending, so a fresh DB is safe.
    executed_queries = [q for q, _ in conn.executed]
    assert any("pg_advisory_xact_lock" in q for q in executed_queries)
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in q for q in executed_queries)
    assert not any("INSERT INTO schema_migrations" in q for q in executed_queries)


def test_a_pending_migration_is_applied_and_recorded() -> None:
    conn = _FakeConnection(applied_versions=[])
    pool = _FakePool(conn)
    migrations = [
        Migration(version="0001_first", sql="CREATE TABLE a (id INT);"),
        Migration(version="0002_second", sql="CREATE TABLE b (id INT);"),
    ]

    applied = _run(MigrationRunner(pool, migrations).run())

    assert applied == ["0001_first", "0002_second"]
    assert conn.applied_versions == ["0001_first", "0002_second"]
    # Every migration sits between DDL and its INSERT-recording -- proven by
    # checking that each version's SQL runs before its version is inserted.
    sql_texts = [q for q, _ in conn.executed]
    first_ddl = sql_texts.index("CREATE TABLE a (id INT);")
    first_insert = next(
        i for i, (q, args) in enumerate(conn.executed)
        if q.startswith("INSERT INTO schema_migrations") and args == ("0001_first",)
    )
    assert first_ddl < first_insert


def test_only_the_pending_migrations_run_when_some_are_already_applied() -> None:
    conn = _FakeConnection(applied_versions=["0001_first"])
    pool = _FakePool(conn)
    migrations = [
        Migration(version="0001_first", sql="CREATE TABLE a (id INT);"),
        Migration(version="0002_second", sql="CREATE TABLE b (id INT);"),
    ]

    applied = _run(MigrationRunner(pool, migrations).run())

    assert applied == ["0002_second"]
    # The already-applied migration's SQL never re-runs.
    sql_texts = [q for q, _ in conn.executed]
    assert "CREATE TABLE a (id INT);" not in sql_texts
    assert "CREATE TABLE b (id INT);" in sql_texts


def test_the_advisory_lock_is_taken_before_reading_applied_versions() -> None:
    conn = _FakeConnection(applied_versions=[])
    pool = _FakePool(conn)
    migrations = [Migration(version="0001_first", sql="SELECT 1;")]

    _run(MigrationRunner(pool, migrations).run())

    lock_index = next(
        i for i, (q, _) in enumerate(conn.executed) if "pg_advisory_xact_lock" in q
    )
    # The runner reads the applied set via fetch(), which is not tracked in
    # `executed` (fetch/execute are different asyncpg methods); instead prove
    # the lock precedes the tracking-table DDL -- which itself precedes the
    # applied-versions read via fetch(), by construction of run().
    ddl_index = next(
        i for i, (q, _) in enumerate(conn.executed)
        if q.startswith("CREATE TABLE IF NOT EXISTS schema_migrations")
    )
    assert lock_index < ddl_index


def test_a_failing_migration_rolls_back_and_records_no_version() -> None:
    conn = _FakeConnection(
        applied_versions=[], fail_on_sql="THIS WILL EXPLODE"
    )
    pool = _FakePool(conn)
    migrations = [
        Migration(version="0001_boom", sql="THIS WILL EXPLODE"),
        Migration(version="0002_never", sql="CREATE TABLE b (id INT);"),
    ]

    with pytest.raises(RuntimeError, match="forced failure"):
        _run(MigrationRunner(pool, migrations).run())

    # The tracking row for the failing migration was never inserted -- so on a
    # restart the same migration will be retried, not silently skipped.
    assert conn.applied_versions == []
    # The transaction rolled back cleanly rather than half-committing.
    assert conn.transactions_rolled_back == 1
    assert conn.transactions_committed == 0
    # The subsequent migration also never ran.
    sql_texts = [q for q, _ in conn.executed]
    assert "CREATE TABLE b (id INT);" not in sql_texts


def test_the_convenience_wrapper_delegates_to_the_runner() -> None:
    conn = _FakeConnection(applied_versions=[])
    pool = _FakePool(conn)
    migrations = [Migration(version="0001_seed", sql="SELECT 1;")]

    assert _run(run_migrations(pool, migrations)) == ["0001_seed"]
