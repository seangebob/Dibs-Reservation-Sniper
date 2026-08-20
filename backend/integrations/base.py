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
