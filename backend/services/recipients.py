"""Who to tell when a watch reaches a terminal state (Milestone 6).

`NotificationService.notify` receives a `Watch`, and a `Watch` deliberately
carries no `user_id` -- Milestone 5 kept account ownership in the durable
projection so the public model and the Milestone 4 contract-drift test would
stay untouched. So an address is resolved the same way ownership is enforced:
start from the watch id, read the projection, then read the account.

This composes two methods Milestone 5 already shipped rather than adding a
query path of its own. It costs one extra round trip versus a join; if that ever
matters, a joined implementation is a drop-in behind `RecipientResolver`.

Like the repositories, this raises on an underlying failure rather than
swallowing it -- `WatchService._notify` is the layer that isolates outbound
failures from a committed transition.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from backend.models.account import User


__all__ = ["AccountRecipientResolver", "RecipientResolver"]


class RecipientResolver(Protocol):
    """Resolve a watch to the address that should hear about it."""

    async def email_for_watch(self, watch_id: str) -> str | None: ...


class _AccountOwnerReader(Protocol):
    """The one method needed from the history projection.

    Structural, so this module imports no repository -- keeping the dependency
    arrow services -> db rather than the other way around, exactly as
    `watch_history.py` does for its readiness tracker.
    """

    async def get_account_owner(self, watch_id: str) -> UUID | None: ...


class _AccountReader(Protocol):
    """The one method needed from the account repository."""

    async def get_by_id(self, user_id: UUID) -> User | None: ...


class AccountRecipientResolver:
    """Watch id -> owning account's email, or None when there is nobody to tell."""

    def __init__(self, history: _AccountOwnerReader, accounts: _AccountReader) -> None:
        self._history = history
        self._accounts = accounts

    async def email_for_watch(self, watch_id: str) -> str | None:
        """Return the owner's address, or None.

        None is an ordinary outcome, not a failure: an anonymous watch has no
        account (Requirement 2.2), and an account deleted since the watch was
        created has no address (Requirement 2.4). Neither is worth an error --
        there is simply nobody to email.
        """

        user_id = await self._history.get_account_owner(watch_id)
        if user_id is None:
            return None
        user = await self._accounts.get_by_id(user_id)
        if user is None:
            return None
        return user.email
