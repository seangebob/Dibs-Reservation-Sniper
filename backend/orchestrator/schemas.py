"""Strict LLM extraction and public orchestrator contracts."""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated, Self


IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
HourMinute = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]
SpecialRequest = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class IntentAction(str, Enum):
    BOOK_RESERVATION = "BOOK_RESERVATION"
    SEARCH_AVAILABILITY = "SEARCH_AVAILABILITY"
    CREATE_WATCH = "CREATE_WATCH"


class VenueType(str, Enum):
    RESTAURANT = "RESTAURANT"
    RECREATION = "RECREATION"
    UNKNOWN = "UNKNOWN"


class IntentStatus(str, Enum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class OrchestratorRoute(str, Enum):
    BOOKING_SERVICE = "BOOKING_SERVICE"
    WATCH_SERVICE = "WATCH_SERVICE"
    CLARIFICATION = "CLARIFICATION"


class MissingField(str, Enum):
    ACTION = "action"
    VENUE_NAME = "venue_name"
    PARTY_SIZE = "party_size"
    DATE = "date"
    TIME = "time"


class TimeWindow(BaseModel):
    """Inclusive local-time window for flexible searches or watches."""

    model_config = ConfigDict(extra="forbid")

    start: HourMinute = Field(description="Earliest acceptable local time")
    end: HourMinute = Field(description="Latest acceptable local time")


class ParseRequest(BaseModel):
    """Raw text accepted from the API gateway or frontend."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(
        min_length=1,
        max_length=2_000,
        description="Untrusted natural-language request from the user",
    )


class ReservationExtraction(BaseModel):
    """Provider output before deterministic business validation.

    Every key is required by the structured-output schema. Unknown values use
    null (or UNKNOWN for venue type) instead of fabricated data.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: IntentAction | None = Field(
        description="Requested booking, availability search, or watch action"
    )
    venue_name: str | None = Field(
        min_length=1,
        max_length=200,
        description="Restaurant, cafe, or recreational venue name",
    )
    venue_type: VenueType = Field(description="Kind of venue, or UNKNOWN")
    party_size: int | None = Field(
        ge=1,
        le=100,
        description="Number of guests or participants",
    )
    date: IsoDate | None = Field(description="Target local date in YYYY-MM-DD")
    preferred_time: HourMinute | None = Field(
        description="Exact preferred local time in 24-hour HH:MM"
    )
    time_window: TimeWindow | None = Field(
        description="Acceptable local-time range when the request is flexible"
    )
    duration_minutes: int | None = Field(
        ge=15,
        le=720,
        description="Requested recreational activity duration when stated",
    )
    special_requests: list[SpecialRequest] = Field(
        max_length=10,
        description="Explicit accessibility, seating, or activity requests",
    )

    @property
    def has_valid_date(self) -> bool:
        """Whether the extracted date is a real calendar day.

        A well-formed but impossible date such as 2026-02-30 is deliberately
        not a validation error here: the model output is untrusted input, so
        the deterministic validator turns it into a clarification rather than
        an upstream provider failure.
        """

        if self.date is None:
            return False
        try:
            date.fromisoformat(self.date)
        except ValueError:
            return False
        return True


class ReservationIntent(BaseModel):
    """Validated orchestration result returned to the API router."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: IntentStatus
    route: OrchestratorRoute
    action: IntentAction | None
    venue_name: str | None = Field(min_length=1, max_length=200)
    venue_type: VenueType
    market: Literal["Kitchener-Waterloo-Cambridge, ON"]
    party_size: int | None = Field(ge=1, le=100)
    date: IsoDate | None
    preferred_time: HourMinute | None
    time_window: TimeWindow | None
    duration_minutes: int | None = Field(ge=15, le=720)
    special_requests: list[SpecialRequest] = Field(max_length=10)
    missing_fields: list[MissingField]
    clarification_question: str | None = Field(min_length=1, max_length=400)

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        has_time = self.preferred_time is not None or self.time_window is not None
        required_values_present = all(
            (
                self.action is not None,
                self.venue_name is not None,
                self.party_size is not None,
                self.date is not None,
                has_time,
            )
        )

        if self.status is IntentStatus.READY:
            if not required_values_present:
                raise ValueError("READY intent is missing required reservation data")
            if self.route is OrchestratorRoute.CLARIFICATION:
                raise ValueError("READY intent cannot route to clarification")
            if self.missing_fields or self.clarification_question is not None:
                raise ValueError("READY intent cannot contain clarification data")
        else:
            if self.route is not OrchestratorRoute.CLARIFICATION:
                raise ValueError("incomplete intent must route to clarification")
            if not self.missing_fields or self.clarification_question is None:
                raise ValueError("incomplete intent requires missing fields and a question")

        return self

    @property
    def is_ready(self) -> bool:
        """Whether downstream services may consume this intent."""

        return self.status is IntentStatus.READY
