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
    """Writes watch transitions to the application log."""

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        logger.info(
            "watch=%s event=%s venue=%s date=%s party=%d attempts=%d",
            watch.watch_id,
            event.value,
            watch.query.venue_name,
            watch.query.date,
            watch.query.party_size,
            watch.attempts,
        )


class RecordingNotificationService:
    """Collects notifications instead of sending them. Used by tests."""

    def __init__(self) -> None:
        self.events: list[tuple[str, WatchEvent]] = []

    async def notify(self, watch: Watch, event: WatchEvent) -> None:
        self.events.append((watch.watch_id, event))
