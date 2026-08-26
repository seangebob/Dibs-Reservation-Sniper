"""Poll pacing for background watches.

A watch that polls on an exact cadence is trivially identifiable as a bot: the
request timestamps form a perfect arithmetic sequence. Spreading each delay
across a window around the base interval both hides that signature and keeps
many watches created at the same moment from stampeding the provider together.
"""

from dataclasses import dataclass
import random

from backend.config import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_POLL_JITTER_SECONDS,
)


#: Absolute floor on a delay, so no schedule can ever hammer a provider. It
#: sits well below the smallest interval `config` accepts (15s), because a
#: floor that bites into a real schedule would clamp every jittered delay to
#: the same number and quietly undo the jitter.
MIN_DELAY_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class PollSchedule:
    """How long to wait between two availability checks for one watch."""

    interval_seconds: float = float(DEFAULT_POLL_INTERVAL_SECONDS)
    jitter_seconds: float = float(DEFAULT_POLL_JITTER_SECONDS)

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds cannot be negative")
        if self.jitter_seconds >= self.interval_seconds:
            raise ValueError("jitter_seconds must be smaller than interval_seconds")

    def next_delay(self, rng: random.Random | None = None) -> float:
        """Return the next delay in seconds, jittered around the interval."""

        source = rng or random
        offset = source.uniform(-self.jitter_seconds, self.jitter_seconds)
        return max(MIN_DELAY_SECONDS, self.interval_seconds + offset)

    @property
    def earliest_delay(self) -> float:
        return max(MIN_DELAY_SECONDS, self.interval_seconds - self.jitter_seconds)

    @property
    def latest_delay(self) -> float:
        return self.interval_seconds + self.jitter_seconds
