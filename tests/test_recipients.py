"""`AccountRecipientResolver`: watch id -> the address that should hear about it.

Fake repositories throughout; no PostgreSQL. The point of these cases is that
"nobody to tell" is an ordinary outcome with several distinct causes, and none
of them may raise into a committed watch transition.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from backend.models.account import User
from backend.services.recipients import AccountRecipientResolver


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _FakeHistory:
    """Projection stand-in: watch id -> account owner, or None if anonymous."""

    def __init__(self, owners: dict[str, UUID] | None = None) -> None:
        self.owners = owners or {}

    async def get_account_owner(self, watch_id: str) -> UUID | None:
        return self.owners.get(watch_id)


class _FakeAccounts:
    def __init__(self, users: dict[UUID, User] | None = None) -> None:
        self.users = users or {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)


class _RaisingHistory:
    async def get_account_owner(self, watch_id: str) -> UUID | None:
        raise RuntimeError("postgres is unreachable")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _resolver(owners=None, users=None) -> AccountRecipientResolver:
    return AccountRecipientResolver(_FakeHistory(owners), _FakeAccounts(users))


def test_an_account_owned_watch_resolves_to_its_owners_address() -> None:
    user_id = uuid4()
    user = User(id=user_id, email="scout@example.com", created_at=NOW)

    resolver = _resolver({"watch_1": user_id}, {user_id: user})

    assert _run(resolver.email_for_watch("watch_1")) == "scout@example.com"


def test_an_anonymous_watch_resolves_to_nobody() -> None:
    """Requirement 2.2: no account, no address, and no error."""

    assert _run(_resolver().email_for_watch("watch_anonymous")) is None


def test_a_watch_absent_from_the_projection_resolves_to_nobody() -> None:
    user_id = uuid4()
    resolver = _resolver({"watch_1": user_id})

    assert _run(resolver.email_for_watch("watch_missing")) is None


def test_a_deleted_account_resolves_to_nobody_rather_than_raising() -> None:
    """Requirement 2.4: the projection still names an owner that no longer
    exists, which is a reason to send nothing, not to fail."""

    user_id = uuid4()
    resolver = _resolver({"watch_1": user_id}, users={})  # owner, but no account

    assert _run(resolver.email_for_watch("watch_1")) is None


def test_only_the_owning_account_is_consulted() -> None:
    mine, theirs = uuid4(), uuid4()
    users = {
        mine: User(id=mine, email="mine@example.com", created_at=NOW),
        theirs: User(id=theirs, email="theirs@example.com", created_at=NOW),
    }
    resolver = _resolver({"watch_1": mine, "watch_2": theirs}, users)

    assert _run(resolver.email_for_watch("watch_1")) == "mine@example.com"
    assert _run(resolver.email_for_watch("watch_2")) == "theirs@example.com"


def test_an_underlying_failure_propagates_for_the_caller_to_isolate() -> None:
    """Deliberate, matching the repositories: this stays a thin data-access
    composition, and `WatchService._notify` is what keeps the failure away from
    the committed transition."""

    resolver = AccountRecipientResolver(_RaisingHistory(), _FakeAccounts())

    with pytest.raises(RuntimeError, match="postgres is unreachable"):
        _run(resolver.email_for_watch("watch_1"))
