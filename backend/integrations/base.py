"""Provider-neutral reservation adapter contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

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


class ReconciliationStatus(str, Enum):
    """The authoritative-ness of a reconciliation answer for one booking key.

    The distinction matters only after a booking has been *attempted*: a plain
    `get_booking(...) == None` cannot tell "no reservation was ever made" apart
    from "the provider has not made the write visible yet", so the service must
    not read absence as definitive. A provider that can answer authoritatively
    returns `DEFINITIVELY_ABSENT`; one that cannot returns `UNKNOWN`.
    """

    CONFIRMED = "CONFIRMED"
    DEFINITIVELY_ABSENT = "DEFINITIVELY_ABSENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: ReconciliationStatus
    confirmation: BookingConfirmation | None = None


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

    async def reconcile_booking(
        self,
        idempotency_key: str,
    ) -> ReconciliationResult:
        """Authoritatively resolve whether a booking exists for one key.

        The default is conservative: a known booking is `CONFIRMED`, but an
        absent one is `UNKNOWN` rather than `DEFINITIVELY_ABSENT`, because a
        generic provider cannot promise that a just-issued reservation is
        already visible. An authoritative provider (the mock) overrides this to
        return `DEFINITIVELY_ABSENT`, which is what makes cancellation-safe
        auto-book possible.
        """

        booking = await self.get_booking(idempotency_key)
        if booking is not None:
            return ReconciliationResult(ReconciliationStatus.CONFIRMED, booking)
        return ReconciliationResult(ReconciliationStatus.UNKNOWN)
