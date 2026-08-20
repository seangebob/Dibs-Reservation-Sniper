"""Domain models shared by backend services and integrations."""

from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
    BookingStatus,
    ExecutionStatus,
    PromptExecutionResult,
)

__all__ = [
    "AvailabilityQuery",
    "AvailabilitySlot",
    "BookingConfirmation",
    "BookingStatus",
    "ExecutionStatus",
    "PromptExecutionResult",
]
