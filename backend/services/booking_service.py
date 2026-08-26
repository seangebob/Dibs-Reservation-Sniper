"""Business logic connecting validated intents to reservation adapters."""

import json
from hashlib import sha256

from backend.integrations.base import (
    ReservationAdapter,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.models.reservation import (
    AvailabilityQuery,
    ExecutionStatus,
    PromptExecutionResult,
)
from backend.orchestrator.schemas import (
    IntentAction,
    OrchestratorRoute,
    ReservationIntent,
)


class BookingService:
    """Executes safe Milestone 2 search and mock-booking behavior."""

    def __init__(self, adapter: ReservationAdapter) -> None:
        self._adapter = adapter

    async def execute(self, intent: ReservationIntent) -> PromptExecutionResult:
        if not intent.is_ready:
            return PromptExecutionResult(
                status=ExecutionStatus.CLARIFICATION_REQUIRED,
                intent=intent,
                slots=[],
                booking=None,
                message=intent.clarification_question or "More information is required.",
            )

        if intent.route is OrchestratorRoute.WATCH_SERVICE:
            return PromptExecutionResult(
                status=ExecutionStatus.WATCH_REQUIRED,
                intent=intent,
                slots=[],
                booking=None,
                message=(
                    "This request needs background monitoring. Route it through "
                    "the watch service rather than the booking service."
                ),
            )

        query = self._query_from(intent)
        idempotency_key: str | None = None
        if intent.action is IntentAction.BOOK_RESERVATION:
            idempotency_key = self._idempotency_key(intent, query)
            replayed = await self._replayed_booking(intent, idempotency_key)
            if replayed is not None:
                return replayed

        slots = await self._adapter.search_availability(query)
        if not slots:
            # An identical request may have booked the only matching slot while
            # this one was searching. That is the same reservation, not a
            # sold-out venue, so the existing confirmation is returned.
            replayed = await self._replayed_booking(intent, idempotency_key)
            if replayed is not None:
                return replayed

            return PromptExecutionResult(
                status=ExecutionStatus.NO_AVAILABILITY,
                intent=intent,
                slots=[],
                booking=None,
                message="No availability matched the request. (mock search only returns one slot for testing purposes.)",
            )

        if intent.action is IntentAction.SEARCH_AVAILABILITY:
            return PromptExecutionResult(
                status=ExecutionStatus.AVAILABILITY_FOUND,
                intent=intent,
                slots=slots,
                booking=None,
                message=f"Found {len(slots)} mock availability slot(s).",
            )

        if intent.action is not IntentAction.BOOK_RESERVATION or idempotency_key is None:
            raise ValueError(f"Unsupported booking-service action: {intent.action}")

        # A slot can be taken between the search and the booking call, so a
        # lost race falls through to the next slot instead of surfacing as an
        # adapter error to the caller.
        for slot in slots:
            try:
                confirmation = await self._adapter.book_slot(
                    slot.slot_id,
                    idempotency_key=idempotency_key,
                )
            except (SlotUnavailableError, SlotNotFoundError):
                continue

            return PromptExecutionResult(
                status=ExecutionStatus.MOCK_BOOKED,
                intent=intent,
                slots=slots,
                booking=confirmation,
                message=(
                    "Mock reservation confirmed. No real venue or booking provider "
                    "was contacted."
                ),
            )

        replayed = await self._replayed_booking(intent, idempotency_key)
        if replayed is not None:
            return replayed

        return PromptExecutionResult(
            status=ExecutionStatus.NO_AVAILABILITY,
            intent=intent,
            slots=[],
            booking=None,
            message=(
                "Every matching mock slot was taken before the booking "
                "completed."
            ),
        )

    async def _replayed_booking(
        self,
        intent: ReservationIntent,
        idempotency_key: str | None,
    ) -> PromptExecutionResult | None:
        """Return the confirmation an equivalent request already created."""

        if idempotency_key is None:
            return None
        existing = await self._adapter.get_booking(idempotency_key)
        if existing is None:
            return None

        return PromptExecutionResult(
            status=ExecutionStatus.MOCK_BOOKED,
            intent=intent,
            slots=[existing.slot],
            booking=existing,
            message=(
                "Returning the existing mock reservation for this idempotent "
                "request. No real venue or booking provider was contacted."
            ),
        )

    @staticmethod
    def _idempotency_key(
        intent: ReservationIntent,
        query: AvailabilityQuery,
    ) -> str:
        identity = {
            "action": intent.action.value if intent.action is not None else None,
            "venue_name": query.venue_name.casefold(),
            "venue_type": query.venue_type.value,
            "market": query.market,
            "party_size": query.party_size,
            "date": query.date,
            "preferred_time": query.preferred_time,
            "time_window": (
                query.time_window.model_dump() if query.time_window is not None else None
            ),
            "duration_minutes": query.duration_minutes,
            "special_requests": sorted(
                request.casefold() for request in query.special_requests
            ),
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _query_from(intent: ReservationIntent) -> AvailabilityQuery:
        if (
            intent.venue_name is None
            or intent.party_size is None
            or intent.date is None
        ):
            raise ValueError("ready intent is missing required booking parameters")

        return AvailabilityQuery(
            venue_name=intent.venue_name,
            venue_type=intent.venue_type,
            market=intent.market,
            party_size=intent.party_size,
            date=intent.date,
            preferred_time=intent.preferred_time,
            time_window=intent.time_window,
            duration_minutes=intent.duration_minutes,
            special_requests=intent.special_requests,
        )
