"""PostgreSQL connection pool and migration runner for the watch-history projection.

The pool and the runner are the two seams every downstream Milestone 4 task
(`WatchHistoryRepository`, health readiness, listing scope) sits on top of.
Both are deliberately narrow: the pool is a thin factory around ``asyncpg``, and
the runner applies ordered ``.sql`` files under a Postgres advisory lock so two
processes racing at startup cannot try to apply the same migration twice.

Nothing here is on the live-watch hot path. If the pool cannot be created or
the migration cannot be applied, startup fails with ``ConfigurationError`` per
Requirement 3.4; a Postgres outage after successful startup degrades a health
signal (Task 7) rather than failing any watch operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import asyncpg

from backend.config import ConfigurationError, PostgresSettings


__all__ = [
    "MIGRATIONS_DIRECTORY",
    "Migration",
    "MigrationRunner",
    "PoolLike",
    "create_pool",
    "discover_migrations",
    "run_migrations",
]


#: Location of the versioned ``.sql`` files, next to this module so migrations
#: ship with the code that reads them rather than as a separate deploy artifact.
MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"

#: A distinct 64-bit key so this migration lock cannot collide with any other
#: advisory lock a different subsystem might take in the same database.
#: Value is arbitrary; changing it would strand a mid-migration lock holder.
_MIGRATION_ADVISORY_LOCK_KEY = 0x0D1B5_4CE_D1B5_4CE  # "dibs..." in hex-ish


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered migration file discovered on disk."""

    version: str
    sql: str


class _ConnectionLike(Protocol):
    """The subset of an ``asyncpg`` connection this module actually uses.

    Structural so tests can inject a fake without importing asyncpg types.
    """

    async def execute(self, query: str, *args: object) -> object: ...
    async def fetchval(self, query: str, *args: object) -> object: ...
    # Each row is an `asyncpg.Record` in production (or a dict-like fake in
    # tests) -- both support `row["column"]`, so `Any` is the honest element
    # type rather than an unindexable `object`.
    async def fetch(self, query: str, *args: object) -> list[Any]: ...


class _AcquireContext(Protocol):
    async def __aenter__(self) -> _ConnectionLike: ...
    async def __aexit__(self, *exc: object) -> None: ...


class _TransactionContext(Protocol):
    async def __aenter__(self) -> object: ...
    async def __aexit__(self, *exc: object) -> None: ...


class PoolLike(Protocol):
    """The subset of an ``asyncpg`` pool the runner and repository use.

    Every Postgres-touching call goes through this shape, so a fake pool with
    the same methods substitutes cleanly in tests without a live database.
    """

    def acquire(self) -> _AcquireContext: ...
    async def close(self) -> None: ...


async def create_pool(settings: PostgresSettings) -> asyncpg.Pool:
    """Open an ``asyncpg`` pool using validated `PostgresSettings`.

    Requires ``settings.enabled``; the caller (lifespan wiring in Task 4) is
    responsible for the "disabled" branch. Raises ``ConfigurationError`` when
    the DSN parses but the server refuses the connection, so a bad password or
    unreachable host surfaces at startup rather than as a projection failure on
    the first user request.
    """

    if not settings.enabled:
        raise ConfigurationError(
            "create_pool called without a POSTGRES_URL. Check enabled first."
        )
    # A statement timeout is a server-side ceiling on every query the pool
    # runs, so a hung projection cannot occupy a connection indefinitely.
    server_settings = {
        "statement_timeout": f"{settings.statement_timeout_seconds * 1000}",
    }
    try:
        pool = await asyncpg.create_pool(
            dsn=settings.dsn,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            server_settings=server_settings,
        )
    except (OSError, asyncpg.PostgresError) as exc:
        raise ConfigurationError(
            f"Could not connect to PostgreSQL at {settings.dsn!r}: {exc}"
        ) from exc
    if pool is None:
        # asyncpg.create_pool is typed to return `Pool | None` for the case
        # where init hooks reject a connection; treat that as a startup error.
        raise ConfigurationError(
            "asyncpg returned no pool for the provided POSTGRES_URL"
        )
    return pool


def discover_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> list[Migration]:
    """List the ordered ``.sql`` migration files in ``directory``.

    Files are ordered by their filename, which is why every migration filename
    starts with a zero-padded numeric prefix. Non-``.sql`` files (README, the
    ``.gitkeep`` placeholder) are ignored so an unrelated file cannot silently
    become a migration.
    """

    if not directory.is_dir():
        return []
    return [
        Migration(version=path.stem, sql=path.read_text(encoding="utf-8"))
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix == ".sql"
    ]


class MigrationRunner:
    """Applies pending migrations under a Postgres advisory lock.

    The runner tracks applied versions in a ``schema_migrations`` table it
    creates on first use. It takes a transaction-scoped advisory lock before
    reading that table so two API replicas starting at once cannot both decide
    a migration is pending and apply it twice.
    """

    def __init__(self, pool: PoolLike, migrations: list[Migration] | None = None):
        self._pool = pool
        self._migrations = (
            migrations if migrations is not None else discover_migrations()
        )

    async def run(self) -> list[str]:
        """Apply every pending migration; return the versions actually applied.

        Returning the applied-list (rather than a bool) makes the runner
        directly testable and lets the lifespan wiring log exactly what ran.
        """

        applied: list[str] = []
        async with self._pool.acquire() as conn:
            # Advisory lock scoped to this transaction: automatically released
            # when the transaction ends, so a crashed migrator cannot wedge
            # every future startup.
            async with _transaction(conn):
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    _MIGRATION_ADVISORY_LOCK_KEY,
                )
                await conn.execute(_SCHEMA_MIGRATIONS_DDL)
                already = await _applied_versions(conn)

                for migration in self._migrations:
                    if migration.version in already:
                        continue
                    await conn.execute(migration.sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)",
                        migration.version,
                    )
                    applied.append(migration.version)
        return applied


async def run_migrations(
    pool: PoolLike,
    migrations: list[Migration] | None = None,
) -> list[str]:
    """Convenience wrapper the lifespan calls once at startup."""

    return await MigrationRunner(pool, migrations).run()


# -- internals --------------------------------------------------------------


_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()


def _transaction(conn: _ConnectionLike) -> _TransactionContext:
    """Return a transaction context for ``conn``.

    Split out so a fake connection in tests exposes ``transaction()`` too
    without needing to inherit from the asyncpg types.
    """

    return conn.transaction()  # type: ignore[attr-defined]


async def _applied_versions(conn: _ConnectionLike) -> set[str]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}
