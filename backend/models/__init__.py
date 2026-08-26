"""Domain models shared by backend services and integrations."""

from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
    ExecutionStatus,
    PromptExecutionResult,
)
from backend.models.watch import (
    Watch,
    WatchPollOutcome,
    WatchPollResult,
    WatchStatus,
)

__all__ = [
    "AvailabilityQuery",
    "AvailabilitySlot",
    "BookingConfirmation",
    "BookingStatus",
    "ExecutionStatus",
    "PromptExecutionResult",
    "Watch",
    "WatchPollOutcome",
    "WatchPollResult",
    "WatchStatus",
]
