"""Availability policy derivation: lifetime-aware attempt budgets.

The headline milestone-3 defect is that a watch used a fixed 200-attempt
ceiling regardless of how long it needed to live, so a multi-day watch went
EXPIRED roughly ten hours in -- long before its reservation date. The policy
factory derives the number of availability checks a watch actually needs from
its remaining lifetime and the earliest normal delay, then caps that at a
finite safety ceiling and reports whether the ceiling still reaches the
deadline.
"""

from datetime import UTC, datetime, timedelta
from fractions import Fraction
import math

import pytest

from backend.services.watch_policy import (
    AvailabilityPolicy,
    AvailabilityPolicyFactory,
    PolicyLimit,
)
from backend.workers.scheduler import PollSchedule


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DEFAULT_SCHEDULE = PollSchedule(interval_seconds=180, jitter_seconds=30)
#: The current default safety ceiling after the milestone-3 config change.
DEFAULT_CEILING = 25_000
#: The old fixed count the bug shipped with.
LEGACY_CEILING = 200


def _earliest_us(schedule: PollSchedule) -> int:
    return int(round(schedule.earliest_delay * 1_000_000))


def _expected_required(
    schedule: PollSchedule,
    now: datetime,
    expires_at: datetime,
) -> int:
    """The true 1 + ceil(remaining / earliest_delay), computed exactly."""

    remaining_us = max(0, (expires_at - now) // timedelta(microseconds=1))
    interval_count = math.ceil(Fraction(remaining_us, _earliest_us(schedule)))
    return interval_count + 1


# Offsets that all resolve to a deadline-capable watch under the default
# ceiling, spanning the same reservation horizons the requirements call out.
OFFSETS = [
    pytest.param(timedelta(hours=8), id="same-day"),
    pytest.param(timedelta(days=1), id="plus-1-day"),
    pytest.param(timedelta(days=7), id="plus-7-days"),
    pytest.param(timedelta(days=30), id="plus-30-days"),
]


# --------------------------------------------------------------------------
# Property 1: Bug Condition - deadline-capable, checked lifetime derivation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("offset", OFFSETS)
def test_required_allowance_is_one_more_than_ceil_of_lifetime_over_delay(
    offset: timedelta,
) -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    expires_at = NOW + offset
    expected = _expected_required(DEFAULT_SCHEDULE, NOW, expires_at)

    policy = factory.derive(
        now=NOW,
        expires_at=expires_at,
        safety_ceiling=DEFAULT_CEILING,
    )

    assert policy.required_attempts == expected
    # The "+1" is the immediate, un-jittered first check.
    assert policy.required_attempts >= 2


@pytest.mark.parametrize("offset", OFFSETS)
def test_default_ceiling_keeps_supported_offsets_deadline_capable(
    offset: timedelta,
) -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    expires_at = NOW + offset

    policy = factory.derive(
        now=NOW,
        expires_at=expires_at,
        safety_ceiling=DEFAULT_CEILING,
    )

    assert policy.effective_attempts == policy.required_attempts
    assert policy.supports_deadline is True
    assert policy.limiting_reason is PolicyLimit.CALENDAR


def test_the_legacy_200_ceiling_expires_a_month_long_watch_early() -> None:
    """This is the bug: the old fixed ceiling could not reach the deadline."""

    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    expires_at = NOW + timedelta(days=30)

    legacy = factory.derive(
        now=NOW, expires_at=expires_at, safety_ceiling=LEGACY_CEILING
    )
    fixed = factory.derive(
        now=NOW, expires_at=expires_at, safety_ceiling=DEFAULT_CEILING
    )

    # A +30-day watch needs far more than 200 checks at 150s earliest cadence.
    assert legacy.required_attempts > LEGACY_CEILING
    assert legacy.effective_attempts == LEGACY_CEILING
    assert legacy.supports_deadline is False
    assert legacy.limiting_reason is PolicyLimit.SAFETY_CEILING
    # The raised default ceiling reaches the deadline for the same watch.
    assert fixed.supports_deadline is True


def test_an_intentionally_short_ceiling_is_retained_and_disclosed() -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    expires_at = NOW + timedelta(days=7)

    policy = factory.derive(now=NOW, expires_at=expires_at, safety_ceiling=100)

    assert policy.effective_attempts == 100
    assert policy.required_attempts > 100
    assert policy.supports_deadline is False
    assert policy.limiting_reason is PolicyLimit.SAFETY_CEILING


def test_a_hot_cadence_over_a_long_horizon_exceeds_the_default_ceiling() -> None:
    hot = PollSchedule(interval_seconds=15, jitter_seconds=0)
    factory = AvailabilityPolicyFactory(hot)
    expires_at = NOW + timedelta(days=30)

    policy = factory.derive(
        now=NOW, expires_at=expires_at, safety_ceiling=DEFAULT_CEILING
    )

    assert policy.required_attempts > DEFAULT_CEILING
    assert policy.effective_attempts == DEFAULT_CEILING
    assert policy.supports_deadline is False


def test_zero_remaining_lifetime_needs_only_the_immediate_check() -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)

    policy = factory.derive(
        now=NOW, expires_at=NOW, safety_ceiling=DEFAULT_CEILING
    )

    assert policy.required_attempts == 1
    assert policy.effective_attempts == 1
    assert policy.supports_deadline is True


def test_a_past_expiry_never_produces_a_negative_allowance() -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)

    policy = factory.derive(
        now=NOW,
        expires_at=NOW - timedelta(hours=1),
        safety_ceiling=DEFAULT_CEILING,
    )

    assert policy.required_attempts == 1
    assert policy.effective_attempts == 1


def test_a_huge_ceiling_and_long_horizon_stay_finite_and_checked() -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    expires_at = NOW + timedelta(days=300)

    policy = factory.derive(
        now=NOW, expires_at=expires_at, safety_ceiling=1_000_000
    )

    assert isinstance(policy.required_attempts, int)
    assert isinstance(policy.effective_attempts, int)
    assert policy.effective_attempts == min(policy.required_attempts, 1_000_000)


def test_derive_requires_timezone_aware_instants() -> None:
    factory = AvailabilityPolicyFactory(DEFAULT_SCHEDULE)
    naive = datetime(2026, 9, 1, 12, 0)

    with pytest.raises((ValueError, AssertionError)):
        factory.derive(now=naive, expires_at=NOW, safety_ceiling=DEFAULT_CEILING)


# --------------------------------------------------------------------------
# Formatting surface reused by the route headers and PromptRouter
# --------------------------------------------------------------------------


def test_policy_header_value_reflects_deadline_support() -> None:
    deadline = AvailabilityPolicy(
        required_attempts=10,
        effective_attempts=10,
        supports_deadline=True,
        limiting_reason=PolicyLimit.CALENDAR,
    )
    limited = AvailabilityPolicy(
        required_attempts=500,
        effective_attempts=100,
        supports_deadline=False,
        limiting_reason=PolicyLimit.SAFETY_CEILING,
    )

    assert deadline.monitoring_policy_header == "deadline"
    assert limited.monitoring_policy_header == "attempt-limited"
    assert deadline.is_attempt_limited is False
    assert limited.is_attempt_limited is True
