"""Best-effort sliding-window throttling (-> 429).

One in-process counter, used for two things:

- **Failed logins** (Milestone 5, Req 6.4): only failures count and a success
  clears the window, so a user who mistypes a password and then gets it right is
  never penalized, and a third party hammering someone else's email cannot lock
  that account out for more than the short window.
- **The paid prompt endpoint** (Milestone 6): every request counts, because the
  cost is incurred whether or not the call succeeds.

Deliberately best-effort rather than a hard quota in either case:

- It is in-process, so N application processes each allow the threshold
  independently -- it blunts abuse, it does not bound it globally.
- It fails open past `_MAX_TRACKED_KEYS` live keys, so an attacker cycling keys
  can never turn the throttle itself into a memory-exhaustion vector.

ponytail: per-process dict; move the counter to Redis if one global limit
across processes ever matters.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


__all__ = [
    "RateLimitedError",
    "SlidingWindowThrottle",
    "TooManyLoginAttemptsError",
    "throttle_key",
]


class RateLimitedError(Exception):
    """Too many events for this key inside the window (-> 429)."""


class TooManyLoginAttemptsError(RateLimitedError):
    """Too many recent failed logins for this email+origin (-> 429)."""

    def __init__(self) -> None:
        super().__init__("Too many failed login attempts. Try again shortly.")


#: A ceiling on distinct tracked keys. Past it the throttle stops tracking new
#: keys rather than growing without bound (see the fail-open note above).
_MAX_TRACKED_KEYS = 10_000


def throttle_key(identity: str, origin: str | None) -> str:
    """Bucket events by caller identity and calling origin.

    ``identity`` is normalized (trimmed, lower-cased) so ``A@X.com`` and
    ``a@x.com`` share one window rather than doubling the allowance; the same
    normalization is harmless for an opaque client id. A caller with no `Origin`
    header (every non-browser client) shares the one bucket for that identity,
    which is the identity-wide limit.
    """

    return f"{identity.strip().lower()}|{origin or '-'}"


class SlidingWindowThrottle:
    """Sliding-window counter of recent events, keyed by caller."""

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: int,
        on_limit: Callable[[], RateLimitedError] = RateLimitedError,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._on_limit = on_limit
        # Monotonic: a clock adjustment must not widen or void a window.
        self._clock = clock or time.monotonic
        self._failures: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        """Raise if ``key`` has already spent its allowance this window."""

        attempts = self._live(key)
        if attempts is not None and len(attempts) >= self._max_events:
            raise self._on_limit()

    def record(self, key: str) -> None:
        """Count one event against ``key``."""

        attempts = self._live(key)
        if attempts is None:
            if len(self._failures) >= _MAX_TRACKED_KEYS:
                self._sweep()
            if len(self._failures) >= _MAX_TRACKED_KEYS:
                return  # fail open rather than grow without bound
            attempts = deque()
            self._failures[key] = attempts
        attempts.append(self._clock())

    def reset(self, key: str) -> None:
        """Forget ``key``'s events -- used on a successful login, so a fumbled
        password that is then corrected costs nothing. Not used by quota-style
        callers, where every event counts."""

        self._failures.pop(key, None)

    # -- internals ----------------------------------------------------------

    def _live(self, key: str) -> deque[float] | None:
        """Return ``key``'s events still inside the window, or None when it has
        none left. Drops the key entirely once its window drains, which is what
        keeps idle keys from accumulating."""

        attempts = self._failures.get(key)
        if attempts is None:
            return None
        cutoff = self._clock() - self._window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def _sweep(self) -> None:
        """Drop every key whose window has fully drained."""

        for key in list(self._failures):
            self._live(key)
