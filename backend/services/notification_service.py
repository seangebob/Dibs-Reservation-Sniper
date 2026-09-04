"""Outbound notification boundary for watch events.

Milestone 3 only needs somewhere for the worker to announce that a watch
changed. Email and push delivery arrive with `integrations/email.py` later; the
protocol is here now so the worker never has to change when they do.
"""

import logging
from enum import Enum
from typing import Protocol

from backend.models.watch import Watch


logger = logging.getLogger(__name__)


class WatchEvent(str, Enum):
    """Why a watch is worth telling its owner about."""

    AVAILABILITY_FOUND = "AVAILABILITY_FOUND"
    BOOKED = "BOOKED"
    EXPIRED = "EXPIRED"


class NotificationService(Protocol):
    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        """Announce one watch transition."""
        ...


class LoggingNotificationService:
    """Writes watch transitions to the application log.

    The line carries only the watch id, the event, and the attempt count --
    enough to correlate a transition with a delivery. The venue, date, and party
    size were removed in Milestone 6: they are someone's reservation, they were
    never needed to operate the system, and a log aggregator is the wrong place
    for them (Requirement 6.1).
    """

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        logger.info(
            "watch=%s event=%s attempts=%d",
            watch.watch_id,
            event.value,
            watch.attempts,
        )


class RecordingNotificationService:
    """Collects notifications instead of sending them. Used by tests."""

    def __init__(self) -> None:
        self.events: list[tuple[str, WatchEvent]] = []

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        self.events.append((watch.watch_id, event))
