"""Poll pacing: the jitter that keeps watches from looking like a bot."""

import random

import pytest

from backend.workers.scheduler import MIN_DELAY_SECONDS, PollSchedule


def test_delay_stays_inside_the_jitter_window() -> None:
    schedule = PollSchedule(interval_seconds=180, jitter_seconds=30)
    rng = random.Random(1234)

    delays = [schedule.next_delay(rng) for _ in range(500)]

    assert all(150.0 <= delay <= 210.0 for delay in delays)
    assert min(delays) >= schedule.earliest_delay
    assert max(delays) <= schedule.latest_delay


def test_delays_are_not_a_constant_cadence() -> None:
    """The whole point of jitter: consecutive polls must not be identical."""

    schedule = PollSchedule(interval_seconds=180, jitter_seconds=30)
    rng = random.Random(7)

    delays = [schedule.next_delay(rng) for _ in range(50)]

    assert len(set(delays)) > 40


def test_jitter_spreads_both_sides_of_the_interval() -> None:
    schedule = PollSchedule(interval_seconds=180, jitter_seconds=30)
    rng = random.Random(99)

    delays = [schedule.next_delay(rng) for _ in range(200)]

    assert any(delay < 180 for delay in delays)
    assert any(delay > 180 for delay in delays)


def test_zero_jitter_gives_the_bare_interval() -> None:
    schedule = PollSchedule(interval_seconds=120, jitter_seconds=0)

    assert schedule.next_delay(random.Random(0)) == 120.0


def test_short_interval_never_produces_a_hammering_delay() -> None:
    schedule = PollSchedule(interval_seconds=6, jitter_seconds=5)
    rng = random.Random(3)

    delays = [schedule.next_delay(rng) for _ in range(200)]

    assert min(delays) >= MIN_DELAY_SECONDS


@pytest.mark.parametrize(
    ("interval", "jitter"),
    [(0, 0), (-1, 0), (180, -1), (30, 30), (30, 60)],
)
def test_incoherent_schedules_are_rejected(interval: float, jitter: float) -> None:
    with pytest.raises(ValueError):
        PollSchedule(interval_seconds=interval, jitter_seconds=jitter)
