"""Reservation search, booking, and execution-result contracts."""

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self

from backend.orchestrator.schemas import (
    HourMinute,
    IsoDate,
    ReservationIntent,
    SpecialRequest,
    TimeWindow,
    VenueType,
)


class AvailabilityQuery(BaseModel):
    """Validated parameters passed to a reservation adapter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    venue_name: str = Field(min_length=1, max_length=200)
    venue_type: VenueType
    market: Literal["Kitchener-Waterloo-Cambridge, ON"]
    party_size: int = Field(ge=1, le=100)
    date: IsoDate
    preferred_time: HourMinute | None
    time_window: TimeWindow | None
    duration_minutes: int | None = Field(ge=15, le=720)
    special_requests: list[SpecialRequest] = Field(max_length=10)

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_time_preference(self) -> Self:
        if self.preferred_time is None and self.time_window is None:
            raise ValueError("availability query requires a time or time window")
        if (
            self.preferred_time is not None
            and self.time_window is not None
            and not (
                self.time_window.start
                <= self.preferred_time
                <= self.time_window.end
            )
        ):
            raise ValueError("preferred time must fall inside the time window")
        return self


class AvailabilitySlot(BaseModel):
    """A provider-neutral available reservation slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_id: str = Field(min_length=1, max_length=200)
    provider: Literal["mock"]
    venue_name: str = Field(min_length=1, max_length=200)
    venue_type: VenueType
    date: IsoDate
    start_time: HourMinute
    end_time: HourMinute | None
    party_size: int = Field(ge=1, le=100)
    max_party_size: int = Field(ge=1, le=100)
    available: Literal[True] = True

    @model_validator(mode="after")
    def validate_capacity(self) -> Self:
        if self.party_size > self.max_party_size:
            raise ValueError("slot cannot seat the requested party size")
        return self


class BookingStatus(str, Enum):
    MOCK_CONFIRMED = "MOCK_CONFIRMED"


class BookingConfirmation(BaseModel):
    """Provider-neutral booking confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    booking_id: str = Field(min_length=1, max_length=200)
    provider: Literal["mock"]
    status: BookingStatus
    slot: AvailabilitySlot
    created_at: datetime


class ExecutionStatus(str, Enum):
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    AVAILABILITY_FOUND = "AVAILABILITY_FOUND"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    MOCK_BOOKED = "MOCK_BOOKED"
    WATCH_REQUIRED = "WATCH_REQUIRED"


class PromptExecutionResult(BaseModel):
    """End-to-end result of parsing and safely handling one prompt."""

    model_config = ConfigDict(extra="forbid")

    status: ExecutionStatus
    intent: ReservationIntent
    slots: list[AvailabilitySlot]
    booking: BookingConfirmation | None
    message: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.status is ExecutionStatus.MOCK_BOOKED and self.booking is None:
            raise ValueError("MOCK_BOOKED result requires a confirmation")
        if self.status is not ExecutionStatus.MOCK_BOOKED and self.booking is not None:
            raise ValueError("only MOCK_BOOKED may include a confirmation")
        if self.status is ExecutionStatus.CLARIFICATION_REQUIRED and self.intent.is_ready:
            raise ValueError("clarification result requires an incomplete intent")
        return self
