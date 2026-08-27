"""Deadline-aware availability-attempt policy for watches.

A watch has two clocks: the calendar deadline `expires_at`, and a budget of
availability checks. The bug this fixes let the budget be a fixed 200 for every
watch, so a multi-day watch ran out of checks about ten hours in and went
EXPIRED long before its reservation date.

The policy derives how many checks a watch actually needs -- one immediate
check plus one for every earliest-possible interval remaining until the
deadline -- then caps that at a finite safety ceiling. When the ceiling is high
enough the watch is *deadline-capable* and the calendar decides when it ends;
when an operator sets a deliberately smaller ceiling the watch is
*attempt-limited*, and every creation surface says so rather than promising
monitoring that will not happen.

All arithmetic is integer microseconds. Python integers do not overflow, so
"checked" here means: never truncate through float division, never allocate or
loop proportional to a count, and never emit a negative allowance.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from backend.workers.scheduler import PollSchedule


__all__ = [
    "AvailabilityPolicy",
    "AvailabilityPolicyFactory",
    "PolicyLimit",
]


class PolicyLimit(str, Enum):
    """What bounds a watch's monitoring: its calendar or its safety ceiling."""

    CALENDAR = "CALENDAR"
    SAFETY_CEILING = "SAFETY_CEILING"


@dataclass(frozen=True, slots=True)
class AvailabilityPolicy:
    """The derived attempt budget and whether it reaches the deadline."""

    #: 1 + ceil(remaining_lifetime / earliest_delay): the immediate check plus
    #: one per earliest interval until the deadline.
    required_attempts: int
    #: min(required, safety_ceiling): what the watch is actually granted.
    effective_attempts: int
    supports_deadline: bool
    limiting_reason: PolicyLimit

    @property
    def is_attempt_limited(self) -> bool:
        return not self.supports_deadline

    @property
    def monitoring_policy_header(self) -> str:
        """Value for the ``X-Watch-Monitoring-Policy`` response header."""

        return "deadline" if self.supports_deadline else "attempt-limited"


class AvailabilityPolicyFactory:
    """Derives an `AvailabilityPolicy` from a lifetime and a poll schedule."""

    def __init__(self, schedule: PollSchedule) -> None:
        self._schedule = schedule

    def derive(
        self,
        *,
        now: datetime,
        expires_at: datetime,
        safety_ceiling: int,
    ) -> AvailabilityPolicy:
        if now.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError("policy derivation requires timezone-aware instants")
        if safety_ceiling < 1:
            raise ValueError("safety_ceiling must be a positive integer")

        earliest_us = self._earliest_delay_microseconds()
        remaining_us = max(
            0, (expires_at - now) // timedelta(microseconds=1)
        )
        # Ceil division on non-negative integers, without float rounding.
        interval_count = -(-remaining_us // earliest_us)
        required = interval_count + 1
        effective = min(required, safety_ceiling)
        supports_deadline = effective == required

        return AvailabilityPolicy(
            required_attempts=required,
            effective_attempts=effective,
            supports_deadline=supports_deadline,
            limiting_reason=(
                PolicyLimit.CALENDAR
                if supports_deadline
                else PolicyLimit.SAFETY_CEILING
            ),
        )

    def _earliest_delay_microseconds(self) -> int:
        earliest_us = int(round(self._schedule.earliest_delay * 1_000_000))
        # The schedule already floors earliest_delay at MIN_DELAY_SECONDS, but
        # guard against a zero divisor regardless of how it was constructed.
        return max(1, earliest_us)
