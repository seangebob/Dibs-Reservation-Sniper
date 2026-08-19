"""Validated API and structured-output contracts."""

from datetime import date
import re

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


class ParseRequest(BaseModel):
    """Raw text accepted from the API gateway or client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(
        min_length=1,
        max_length=2_000,
        description="Unstructured reservation request from the user",
    )


class ReservationIntent(BaseModel):
    """Structured reservation data or a targeted clarification request.

    Every key is required in JSON. Unknown values are represented as null rather
    than invented. A result is executable only when all reservation values are
    present and ``missing_info`` is null.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    restaurant: str | None = Field(
        min_length=1,
        max_length=200,
        description="Name of the restaurant, cafe, or recreational venue",
    )
    party_size: int | None = Field(
        ge=1,
        le=50,
        description="Number of guests",
    )
    date: IsoDate | None = Field(
        description="Target local date in YYYY-MM-DD format",
    )
    preferred_time: HourMinute | None = Field(
        description="Target local time in 24-hour HH:MM format",
    )
    missing_info: str | None = Field(
        min_length=1,
        max_length=300,
        description="One targeted question when required details are missing",
    )

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("missing_info")
    @classmethod
    def validate_clarification_is_a_question(cls, value: str | None) -> str | None:
        if value is not None and not re.search(r"\?\s*$", value):
            raise ValueError("missing_info must be a question")
        return value

    @model_validator(mode="after")
    def validate_clarification_state(self) -> Self:
        required_values = (
            self.restaurant,
            self.party_size,
            self.date,
            self.preferred_time,
        )
        has_missing_value = any(value is None for value in required_values)

        if has_missing_value and self.missing_info is None:
            raise ValueError("missing_info is required when reservation data is missing")
        if not has_missing_value and self.missing_info is not None:
            raise ValueError("missing_info must be null when reservation data is complete")
        return self

    @property
    def is_complete(self) -> bool:
        """Whether this intent is safe to pass to later booking logic."""

        return self.missing_info is None
