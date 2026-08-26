"""Persistent watch state for background availability monitoring."""

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
)


class WatchStatus(str, Enum):
    """Lifecycle of a single watch.

    ACTIVE is the only status the queue will keep polling; every other status
    is terminal, which is what stops a re-enqueued task from running forever.
    """

    ACTIVE = "ACTIVE"
    FOUND = "FOUND"
    BOOKED = "BOOKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self is not WatchStatus.ACTIVE


class WatchPollOutcome(str, Enum):
    """What one background poll did."""

    NO_AVAILABILITY = "NO_AVAILABILITY"
    FOUND = "FOUND"
    BOOKED = "BOOKED"
    EXPIRED = "EXPIRED"
    ALREADY_FINISHED = "ALREADY_FINISHED"
    UNKNOWN_WATCH = "UNKNOWN_WATCH"


class Watch(BaseModel):
    """One monitored reservation request and everything the worker needs.

    The watch carries a fully validated `AvailabilityQuery` rather than the raw
    prompt or intent, so a worker never re-runs the language model: replaying a
    watch is deterministic and costs nothing.
    """

    model_config = ConfigDict(extra="forbid")

    watch_id: str = Field(min_length=1, max_length=200)
    status: WatchStatus
    query: AvailabilityQuery
    auto_book: bool = False
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    found_slots: list[AvailabilitySlot] = Field(default_factory=list, max_length=32)
    booking: BookingConfirmation | None = None
    last_error: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.status is WatchStatus.BOOKED and self.booking is None:
            raise ValueError("BOOKED watch requires a confirmation")
        if self.status is WatchStatus.FOUND and not self.found_slots:
            raise ValueError("FOUND watch requires at least one slot")
        if self.status.is_terminal and self.next_check_at is not None:
            raise ValueError("a finished watch cannot have a next check scheduled")
        if self.attempts > self.max_attempts:
            raise ValueError("attempts cannot exceed max_attempts")
        return self

    def is_exhausted(self, now: datetime) -> bool:
        """Whether this watch has run out of either attempts or calendar time."""

        return self.attempts >= self.max_attempts or now >= self.expires_at


class WatchPollResult(BaseModel):
    """Result of one poll, returned to the caller and to the worker."""

    model_config = ConfigDict(extra="forbid")

    outcome: WatchPollOutcome
    watch: Watch | None
    #: Seconds until the next poll, or None when nothing more is scheduled.
    retry_in_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scheduling(self) -> Self:
        if self.outcome is WatchPollOutcome.NO_AVAILABILITY:
            if self.retry_in_seconds is None:
                raise ValueError("a rescheduled poll requires a retry delay")
        elif self.retry_in_seconds is not None:
            raise ValueError("only a rescheduled poll may carry a retry delay")
        if self.outcome is WatchPollOutcome.UNKNOWN_WATCH and self.watch is not None:
            raise ValueError("an unknown watch cannot carry a record")
        if self.outcome is not WatchPollOutcome.UNKNOWN_WATCH and self.watch is None:
            raise ValueError("a poll result requires the watch it acted on")
        return self


def default_expiry(query: AvailabilityQuery, created_at: datetime) -> datetime:
    """Stop watching once the requested sitting can no longer be booked.

    A watch for tonight is worthless tomorrow, so the reservation date itself
    is the natural deadline rather than a fixed TTL.
    """

    target = datetime.fromisoformat(query.date).replace(tzinfo=created_at.tzinfo)
    end_of_day = target + timedelta(days=1)
    return max(end_of_day, created_at + timedelta(minutes=1))
