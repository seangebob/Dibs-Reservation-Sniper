"""Deterministic validation and routing for untrusted LLM extractions."""

from datetime import date, datetime

from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationExtraction,
    ReservationIntent,
    TimeWindow,
)


class IntentValidator:
    """Turns model extraction into a safe, deterministic routing decision."""

    MARKET = "Kitchener-Waterloo, ON"

    def validate(
        self,
        extraction: ReservationExtraction,
        reference_time: datetime,
    ) -> ReservationIntent:
        target_date = extraction.date
        date_is_past = (
            target_date is not None
            and date.fromisoformat(target_date) < reference_time.date()
        )
        if date_is_past:
            target_date = None

        time_window = self._normalize_time_window(extraction.time_window)
        conflicting_time = (
            time_window is not None
            and extraction.preferred_time is not None
            and not (
                time_window.start <= extraction.preferred_time <= time_window.end
            )
        )
        has_time = (
            extraction.preferred_time is not None or time_window is not None
        ) and not conflicting_time

        missing_fields: list[MissingField] = []
        if extraction.action is None:
            missing_fields.append(MissingField.ACTION)
        if extraction.venue_name is None:
            missing_fields.append(MissingField.VENUE_NAME)
        if extraction.party_size is None:
            missing_fields.append(MissingField.PARTY_SIZE)
        if target_date is None:
            missing_fields.append(MissingField.DATE)
        if not has_time:
            missing_fields.append(MissingField.TIME)

        if missing_fields:
            return ReservationIntent(
                status=IntentStatus.NEEDS_CLARIFICATION,
                route=OrchestratorRoute.CLARIFICATION,
                action=extraction.action,
                venue_name=extraction.venue_name,
                venue_type=extraction.venue_type,
                market=self.MARKET,
                party_size=extraction.party_size,
                date=target_date,
                preferred_time=extraction.preferred_time,
                time_window=time_window,
                duration_minutes=extraction.duration_minutes,
                special_requests=extraction.special_requests,
                missing_fields=missing_fields,
                clarification_question=self._build_question(
                    missing_fields,
                    date_is_past=date_is_past,
                    invalid_window=(
                        extraction.time_window is not None and time_window is None
                    ),
                    conflicting_time=conflicting_time,
                ),
            )

        return ReservationIntent(
            status=IntentStatus.READY,
            route=self._route_for(extraction.action),
            action=extraction.action,
            venue_name=extraction.venue_name,
            venue_type=extraction.venue_type,
            market=self.MARKET,
            party_size=extraction.party_size,
            date=target_date,
            preferred_time=extraction.preferred_time,
            time_window=time_window,
            duration_minutes=extraction.duration_minutes,
            special_requests=extraction.special_requests,
            missing_fields=[],
            clarification_question=None,
        )

    @staticmethod
    def _normalize_time_window(window: TimeWindow | None) -> TimeWindow | None:
        if window is None or window.start > window.end:
            return None
        return window

    @staticmethod
    def _route_for(action: IntentAction | None) -> OrchestratorRoute:
        if action is IntentAction.CREATE_WATCH:
            return OrchestratorRoute.WATCH_SERVICE
        return OrchestratorRoute.BOOKING_SERVICE

    @staticmethod
    def _build_question(
        missing_fields: list[MissingField],
        *,
        date_is_past: bool,
        invalid_window: bool,
        conflicting_time: bool,
    ) -> str:
        if missing_fields == [MissingField.DATE] and date_is_past:
            return "That date has already passed. What future date would you like?"
        if missing_fields == [MissingField.TIME] and invalid_window:
            return "The time window is invalid. What time or valid time range works for you?"
        if missing_fields == [MissingField.TIME] and conflicting_time:
            return (
                "The preferred time falls outside the requested time window. "
                "Which time constraint should I use?"
            )

        single_questions = {
            MissingField.ACTION: (
                "Would you like me to check availability, book it, or create a watch?"
            ),
            MissingField.VENUE_NAME: "Which restaurant or recreational venue would you like?",
            MissingField.PARTY_SIZE: "How many guests or participants are there?",
            MissingField.DATE: "What date would you like?",
            MissingField.TIME: "What time or time range works for you?",
        }
        if len(missing_fields) == 1:
            return single_questions[missing_fields[0]]

        labels = {
            MissingField.ACTION: "request type",
            MissingField.VENUE_NAME: "venue",
            MissingField.PARTY_SIZE: "party size",
            MissingField.DATE: "date",
            MissingField.TIME: "time or time range",
        }
        details = [labels[field] for field in missing_fields]
        if len(details) == 2:
            joined = " and ".join(details)
        else:
            joined = ", ".join(details[:-1]) + f", and {details[-1]}"
        return f"Could you provide the {joined}?"
