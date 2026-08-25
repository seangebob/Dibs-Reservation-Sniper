"""Deterministic validation and routing for untrusted LLM extractions."""

from datetime import date, datetime, timedelta

from backend.data.venues import (
    SLOT_INTERVAL_MINUTES,
    VenueResolutionStatus,
    resolve_venue,
)
from backend.orchestrator.schemas import (
    IntentAction,
    IntentStatus,
    MissingField,
    OrchestratorRoute,
    ReservationExtraction,
    ReservationIntent,
    TimeWindow,
    VenueType,
)


class IntentValidator:
    """Turns model extraction into a safe, deterministic routing decision."""

    MARKET = "Kitchener-Waterloo-Cambridge, ON"

    #: Reservations further out than this are refused rather than guessed at.
    MAX_DAYS_AHEAD = 365

    def validate(
        self,
        extraction: ReservationExtraction,
        reference_time: datetime,
    ) -> ReservationIntent:
        today = reference_time.date()
        venue_name, venue_type, venue_candidates = self._resolve_venue(extraction)

        target_date, date_problem = self._resolve_date(extraction, today)
        preferred_time, time_window, time_problem = self._resolve_time(
            extraction,
            reference_time,
            target_date=target_date,
        )
        # A conflicting pair is not a usable preference; a merely malformed
        # window is dropped in favour of the valid preferred time.
        has_time = (
            preferred_time is not None or time_window is not None
        ) and time_problem != "conflicting"

        missing_fields: list[MissingField] = []
        if extraction.action is None:
            missing_fields.append(MissingField.ACTION)
        if venue_name is None or venue_candidates:
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
                venue_name=venue_name,
                venue_type=venue_type,
                market=self.MARKET,
                party_size=extraction.party_size,
                date=target_date,
                preferred_time=preferred_time,
                time_window=time_window,
                duration_minutes=extraction.duration_minutes,
                special_requests=extraction.special_requests,
                missing_fields=missing_fields,
                clarification_question=self._build_question(
                    missing_fields,
                    date_problem=date_problem,
                    time_problem=time_problem,
                    venue_candidates=venue_candidates,
                ),
            )

        return ReservationIntent(
            status=IntentStatus.READY,
            route=self._route_for(extraction.action),
            action=extraction.action,
            venue_name=venue_name,
            venue_type=venue_type,
            market=self.MARKET,
            party_size=extraction.party_size,
            date=target_date,
            preferred_time=preferred_time,
            time_window=time_window,
            duration_minutes=extraction.duration_minutes,
            special_requests=extraction.special_requests,
            missing_fields=[],
            clarification_question=None,
        )

    def _resolve_venue(
        self,
        extraction: ReservationExtraction,
    ) -> tuple[str | None, VenueType, tuple[str, ...]]:
        """Canonicalize a known venue, or report the ambiguous candidates.

        An unrecognized name is passed through untouched so the platform still
        works for venues outside the mock catalog.
        """

        if extraction.venue_name is None:
            return None, extraction.venue_type, ()

        resolution = resolve_venue(extraction.venue_name)
        if resolution.status is VenueResolutionStatus.RESOLVED:
            venue = resolution.venue
            assert venue is not None
            return venue.name, venue.venue_type, ()
        if resolution.status is VenueResolutionStatus.AMBIGUOUS:
            return (
                extraction.venue_name,
                extraction.venue_type,
                tuple(candidate.name for candidate in resolution.candidates),
            )
        return extraction.venue_name, extraction.venue_type, ()

    def _resolve_date(
        self,
        extraction: ReservationExtraction,
        today: date,
    ) -> tuple[str | None, str | None]:
        """Reject impossible, past, and implausibly distant dates."""

        if extraction.date is None:
            return None, None
        if not extraction.has_valid_date:
            return None, "impossible"

        target = date.fromisoformat(extraction.date)
        if target < today:
            return None, "past"
        if target > today + timedelta(days=self.MAX_DAYS_AHEAD):
            return None, "too_far"
        return extraction.date, None

    def _resolve_time(
        self,
        extraction: ReservationExtraction,
        reference_time: datetime,
        *,
        target_date: str | None,
    ) -> tuple[str | None, TimeWindow | None, str | None]:
        """Normalize the time preference against the clock and each other."""

        preferred_time = extraction.preferred_time
        time_window = extraction.time_window
        if time_window is not None and time_window.start > time_window.end:
            return preferred_time, None, "invalid_window"

        if (
            time_window is not None
            and preferred_time is not None
            and not (time_window.start <= preferred_time <= time_window.end)
        ):
            return preferred_time, time_window, "conflicting"

        if target_date is not None and target_date == reference_time.date().isoformat():
            now = reference_time.strftime("%H:%M")
            earliest = self._next_slot_boundary(now)
            if preferred_time is not None and preferred_time < earliest:
                return None, None, "past_time"
            if time_window is not None:
                if time_window.end < earliest:
                    return None, None, "past_time"
                if time_window.start < earliest:
                    time_window = TimeWindow(start=earliest, end=time_window.end)

        return preferred_time, time_window, None

    @staticmethod
    def _next_slot_boundary(now: str) -> str:
        """Round the current local time up to the next bookable boundary."""

        hours, minutes = (int(part) for part in now.split(":"))
        total = hours * 60 + minutes
        rounded = -(-total // SLOT_INTERVAL_MINUTES) * SLOT_INTERVAL_MINUTES
        if rounded >= 24 * 60:
            return "23:59"
        return f"{rounded // 60:02d}:{rounded % 60:02d}"

    @staticmethod
    def _route_for(action: IntentAction | None) -> OrchestratorRoute:
        if action is IntentAction.CREATE_WATCH:
            return OrchestratorRoute.WATCH_SERVICE
        return OrchestratorRoute.BOOKING_SERVICE

    @classmethod
    def _build_question(
        cls,
        missing_fields: list[MissingField],
        *,
        date_problem: str | None,
        time_problem: str | None,
        venue_candidates: tuple[str, ...],
    ) -> str:
        """Ask about the specific problem rather than restating the schema."""

        specific = cls._specific_question(
            missing_fields,
            date_problem=date_problem,
            time_problem=time_problem,
            venue_candidates=venue_candidates,
        )
        if specific is not None:
            return specific

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

    @staticmethod
    def _specific_question(
        missing_fields: list[MissingField],
        *,
        date_problem: str | None,
        time_problem: str | None,
        venue_candidates: tuple[str, ...],
    ) -> str | None:
        """Return a targeted question when exactly one thing went wrong."""

        if missing_fields == [MissingField.VENUE_NAME] and venue_candidates:
            listed = list(venue_candidates[:3])
            if len(listed) == 1:
                choices = listed[0]
            elif len(listed) == 2:
                choices = " or ".join(listed)
            else:
                choices = ", ".join(listed[:-1]) + f", or {listed[-1]}"
            return f"Did you mean {choices}?"

        if missing_fields == [MissingField.DATE]:
            if date_problem == "past":
                return "That date has already passed. What future date would you like?"
            if date_problem == "impossible":
                return "That date does not exist on the calendar. What date would you like?"
            if date_problem == "too_far":
                return (
                    "I can only book up to a year ahead. What date within the "
                    "next year would you like?"
                )

        if missing_fields == [MissingField.TIME]:
            if time_problem == "invalid_window":
                return (
                    "The time window is invalid. What time or valid time range "
                    "works for you?"
                )
            if time_problem == "conflicting":
                return (
                    "The preferred time falls outside the requested time window. "
                    "Which time constraint should I use?"
                )
            if time_problem == "past_time":
                return (
                    "That time has already passed today. What later time works, "
                    "or should I look at another day?"
                )

        return None
