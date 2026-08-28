"""Typed decisions returned by the atomic watch repository operations.

Both the in-memory and (later) the Redis-Lua repository return these exact
types, so the poll state machine, dispatcher, and recovery coordinator map one
set of outcomes regardless of which store is selected. Keeping them here, apart
from either implementation, is what lets a sequential oracle compare the two.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backend.models.watch import Watch
from backend.models.watch_runtime import WatchRuntime


__all__ = [
    "BookingPermit",
    "BookingPermitStatus",
    "ClaimResult",
    "ClaimStatus",
    "CommitResult",
    "CommitStatus",
    "CreateResult",
    "CreateStatus",
    "DispatchClaim",
    "DispatchResult",
    "DispatchStatus",
    "ScheduleMarker",
    "TransitionResult",
    "TransitionStatus",
    "WindowClaim",
]


class CreateStatus(str, Enum):
    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


@dataclass(frozen=True, slots=True)
class CreateResult:
    status: CreateStatus
    watch: Watch | None = None
    runtime: WatchRuntime | None = None


class ClaimStatus(str, Enum):
    """Why a claim attempt did or did not win the window."""

    OWNED = "OWNED"          # this owner holds the window for a lease epoch
    BUSY = "BUSY"            # another unexpired owner holds it
    EARLY = "EARLY"          # the window is not due yet
    STALE = "STALE"          # the requested window is not the current one
    TERMINAL = "TERMINAL"    # the watch already finished
    UNKNOWN = "UNKNOWN"      # no such watch


@dataclass(frozen=True, slots=True)
class WindowClaim:
    """Proof of ownership for one cadence window during one lease epoch."""

    watch: Watch
    runtime: WatchRuntime
    owner_id: str
    window_id: str
    token: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: ClaimStatus
    claim: WindowClaim | None = None


class BookingPermitStatus(str, Enum):
    GRANTED = "GRANTED"
    CANCELLED = "CANCELLED"
    FENCED = "FENCED"


@dataclass(frozen=True, slots=True)
class BookingPermit:
    status: BookingPermitStatus
    permit_id: str | None = None


class CommitStatus(str, Enum):
    COMMITTED = "COMMITTED"
    FENCED = "FENCED"        # a newer revision or a different owner won
    TERMINAL = "TERMINAL"    # the watch finished under us
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CommitResult:
    status: CommitStatus
    watch: Watch | None = None
    #: Deterministic id for a terminal event, issued at most once per terminal
    #: transition; None for a non-terminal successor.
    event_id: str | None = None


class TransitionStatus(str, Enum):
    """Outcome of an unconditional lifecycle transition (cancel/expire)."""

    APPLIED = "APPLIED"
    NOOP = "NOOP"            # already in the requested terminal state
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    FENCED = "FENCED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TransitionResult:
    status: TransitionStatus
    watch: Watch | None = None
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleMarker:
    """The repository-authoritative record of one logical due window."""

    watch_id: str
    window_id: str
    scheduled_for: datetime


class DispatchStatus(str, Enum):
    """Outcome of trying to acquire the single-flight dispatch lease."""

    CLAIMED = "CLAIMED"      # this owner may publish the window to the queue
    BUSY = "BUSY"           # another dispatcher holds an unexpired lease
    STALE = "STALE"         # the marker is no longer the current due window


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    """Proof of the right to publish one schedule marker for one generation.

    `scheduled_for` is the logical due time (used to compute the queue delay),
    kept distinct from `lease_expires_at`, which bounds only how long this
    dispatch attempt fences other dispatchers off the same window.
    """

    watch_id: str
    window_id: str
    scheduled_for: datetime
    owner_id: str
    generation: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: DispatchStatus
    claim: DispatchClaim | None = None
