"""Dispatch a validated intent to the service that should execute it.

The validator already decided *which* route an intent belongs to; this module
owns *who runs it*. Keeping that split means the booking service never learns
about watches and the watch service never learns about prompts.
"""

from typing import Any

from backend.models.reservation import (
    ExecutionStatus,
    PromptExecutionResult,
)
from backend.orchestrator.schemas import OrchestratorRoute, ReservationIntent
from backend.services.booking_service import BookingService


class PromptRouter:
    """Runs one validated intent through booking or background monitoring."""

    def __init__(
        self,
        booking_service: BookingService,
        watch_service: Any,
    ) -> None:
        self._booking_service = booking_service
        self._watch_service = watch_service

    async def execute(self, intent: ReservationIntent) -> PromptExecutionResult:
        if intent.is_ready and intent.route is OrchestratorRoute.WATCH_SERVICE:
            return await self._create_watch(intent)
        return await self._booking_service.execute(intent)

    async def _create_watch(self, intent: ReservationIntent) -> PromptExecutionResult:
        watch = await self._watch_service.create_from_intent(intent)
        return PromptExecutionResult(
            status=ExecutionStatus.WATCH_CREATED,
            intent=intent,
            slots=[],
            booking=None,
            watch_id=watch.watch_id,
            message=(
                f"Watching {watch.query.venue_name} for {watch.query.party_size} "
                f"on {watch.query.date}. Background checks run every few "
                "minutes until a slot opens or the date passes."
            ),
        )
