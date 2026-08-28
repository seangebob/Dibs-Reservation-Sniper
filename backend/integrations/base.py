"""Provider-neutral reservation adapter contract."""

from abc import ABC, abstractmethod

from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
)


class AdapterError(RuntimeError):
    """Base error raised by reservation platform adapters."""


class SlotNotFoundError(AdapterError):
    """Raised when a provider does not recognize a slot identifier."""


class SlotUnavailableError(AdapterError):
    """Raised when a slot is no longer available."""


class ProviderSequenceTimeout(AdapterError):
    """Raised when the owned provider-sequence deadline expires.

    It is an `AdapterError` so it follows the same fenced, no-attempt outage
    path as any other provider failure. Only the service's own asyncio timeout
    is translated into this; an outer `CancelledError` or a Celery time limit
    is deliberately never caught as a provider outage.
    """


class ReservationAdapter(ABC):
    """Interface implemented by mock and future real booking providers."""

    @abstractmethod
    async def search_availability(
        self,
        query: AvailabilityQuery,
    ) -> list[AvailabilitySlot]:
        """Return currently available slots matching a validated query."""

    @abstractmethod
    async def get_booking(
        self,
        idempotency_key: str,
    ) -> BookingConfirmation | None:
        """Return an existing idempotent booking when one is known."""

    @abstractmethod
    async def book_slot(
        self,
        slot_id: str,
        *,
        idempotency_key: str,
    ) -> BookingConfirmation:
        """Book one provider slot idempotently."""
