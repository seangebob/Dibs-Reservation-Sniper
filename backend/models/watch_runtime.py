"""Internal concurrency and policy sidecar for a watch.

The public `Watch` document is what clients see and what old response fixtures
assert against. Everything the coordinated state machine needs that clients
must not see -- the fencing revision, the current cadence window, the outage
counter, the terminal cleanup deadline -- lives here instead, stored under a
separate key and never merged into the public JSON.

`schema_version` is 2 because version 1 is the pre-sidecar world: a watch with
no runtime at all. `migrate_legacy_watch` builds a version-2 sidecar for such a
record without reopening terminal state or inflating a legacy attempt ceiling.
"""

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.watch import Watch


__all__ = [
    "RuntimePhase",
    "WatchRuntime",
    "initial_runtime",
    "migrate_legacy_watch",
    "window_id_for",
]

#: Generous upper bounds. Python integers do not overflow, but persisted values
#: are still validated so a corrupt document cannot smuggle an absurd count.
_MAX_COUNT = 1_000_000_000
_MAX_REQUIRED_ATTEMPTS = 10_000_000_000


class RuntimePhase(str, Enum):
    """Which side of the irreversible booking call a claimed window is on."""

    POLLING = "POLLING"
    BOOKING = "BOOKING"


def window_id_for(watch_id: str, cadence_sequence: int) -> str:
    """The stable identity of one cadence window.

    A window keeps this identity across every physical redelivery, so duplicate
    jobs for the same cadence resolve to the same logical opportunity.
    """

    return f"{watch_id}:{cadence_sequence}"


class WatchRuntime(BaseModel):
    """Sidecar metadata for one watch. Never serialized into public JSON."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2)
    revision: int = Field(default=0, ge=0, le=_MAX_COUNT)
    required_attempts: int = Field(ge=1, le=_MAX_REQUIRED_ATTEMPTS)
    supports_deadline: bool
    consecutive_outages: int = Field(default=0, ge=0, le=_MAX_COUNT)
    cadence_sequence: int = Field(default=0, ge=0, le=_MAX_COUNT)
    window_id: str | None = Field(default=None, max_length=256)
    scheduled_for: datetime | None = None
    phase: RuntimePhase | None = None
    cancel_requested: bool = False
    terminal_delete_at: datetime | None = None

    @field_validator("scheduled_for", "terminal_delete_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("runtime datetimes must be timezone-aware")
        return value


def initial_runtime(
    watch: Watch,
    *,
    required_attempts: int,
    supports_deadline: bool,
    phase: RuntimePhase | None = None,
) -> WatchRuntime:
    """Build the sidecar for a freshly created watch at revision zero."""

    return WatchRuntime(
        revision=0,
        required_attempts=required_attempts,
        supports_deadline=supports_deadline,
        cadence_sequence=0,
        window_id=window_id_for(watch.watch_id, 0),
        scheduled_for=watch.next_check_at,
        phase=phase,
    )


def migrate_legacy_watch(
    watch: Watch,
    *,
    earliest_delay_seconds: float,
    now: datetime,
) -> WatchRuntime:
    """Derive a version-2 sidecar for a watch that predates the sidecar.

    The persisted `max_attempts` is preserved exactly: the migration cannot
    tell whether 200 was a default or an operator's deliberate ceiling, so it
    never raises it. It only records, in `supports_deadline`, whether that
    ceiling still covers the checks the watch needs from here.
    """

    remaining_required_total = watch.attempts + _forward_required(
        watch, earliest_delay_seconds=earliest_delay_seconds, now=now
    )
    supports_deadline = watch.max_attempts >= remaining_required_total

    if watch.status.is_terminal:
        return WatchRuntime(
            revision=0,
            required_attempts=remaining_required_total,
            supports_deadline=supports_deadline,
        )

    return WatchRuntime(
        revision=0,
        required_attempts=remaining_required_total,
        supports_deadline=supports_deadline,
        cadence_sequence=0,
        window_id=window_id_for(watch.watch_id, 0),
        # An active legacy record keeps its due time; a missing one is due now.
        scheduled_for=watch.next_check_at or now,
    )


def _forward_required(
    watch: Watch,
    *,
    earliest_delay_seconds: float,
    now: datetime,
) -> int:
    """1 + ceil(remaining_lifetime / earliest_delay), in checked integers."""

    earliest_us = max(1, int(round(earliest_delay_seconds * 1_000_000)))
    remaining_us = max(0, (watch.expires_at - now) // timedelta(microseconds=1))
    interval_count = -(-remaining_us // earliest_us)
    return interval_count + 1
