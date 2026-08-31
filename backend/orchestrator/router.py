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

    async def execute(
        self,
        intent: ReservationIntent,
        *,
        owner_client_id: str | None = None,
    ) -> PromptExecutionResult:
        if intent.is_ready and intent.route is OrchestratorRoute.WATCH_SERVICE:
            return await self._create_watch(intent, owner_client_id=owner_client_id)
        return await self._booking_service.execute(intent)

    async def _create_watch(
        self,
        intent: ReservationIntent,
        *,
        owner_client_id: str | None,
    ) -> PromptExecutionResult:
        watch = await self._watch_service.create_from_intent(
            intent, owner_client_id=owner_client_id
        )
        policy = self._watch_service.describe_policy(watch)
        return PromptExecutionResult(
            status=ExecutionStatus.WATCH_CREATED,
            intent=intent,
            slots=[],
            booking=None,
            watch_id=watch.watch_id,
            message=self._watch_message(watch, policy),
        )

    @staticmethod
    def _watch_message(watch: Any, policy: Any) -> str:
        """Describe monitoring truthfully for the derived attempt policy.

        A deadline-capable watch keeps the original promise. A watch whose
        safety ceiling stops it before the date says so instead of implying it
        runs until the reservation date.
        """

        opening = (
            f"Watching {watch.query.venue_name} for {watch.query.party_size} "
            f"on {watch.query.date}. "
        )
        if policy.supports_deadline:
            return opening + (
                "Background checks run every few minutes until a slot opens or "
                "the date passes."
            )
        return opening + (
            "Background checks run every few minutes for up to "
            f"{policy.effective_attempts} availability checks; monitoring may "
            f"stop before {watch.query.date}."
        )
