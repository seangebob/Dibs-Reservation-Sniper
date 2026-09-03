"""Best-effort brute-force throttle for failed logins (Milestone 5, Req 6.4).

A per-email+origin sliding window of recent *failed* logins: past the configured
threshold, `/api/auth/login` answers 429 until the window drains.

Deliberately best-effort rather than an account-lockout policy:

- Only failures count, and a successful login clears the window, so a user who
  mistypes a password and then gets it right is never penalized.
- The window is short (minutes), so a third party hammering someone else's email
  cannot lock that account out for long.
- It is in-process, so N application processes each allow the threshold
  independently -- it blunts brute force, it does not bound it globally.
- It fails open past `_MAX_TRACKED_KEYS` live keys, so an attacker cycling
  emails can never turn the throttle itself into a memory-exhaustion vector.

ponytail: per-process dict; move the counter to Redis if one global limit
across processes ever matters.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


__all__ = ["LoginThrottle", "TooManyLoginAttemptsError", "throttle_key"]


class TooManyLoginAttemptsError(Exception):
    """Too many recent failed logins for this email+origin (-> 429)."""

    def __init__(self) -> None:
        super().__init__("Too many failed login attempts. Try again shortly.")


#: A ceiling on distinct tracked keys. Past it the throttle stops tracking new
#: keys rather than growing without bound (see the fail-open note above).
_MAX_TRACKED_KEYS = 10_000


def throttle_key(email: str, origin: str | None) -> str:
    """Bucket failed logins by account and calling origin (Req 6.4).

    The email is normalized the same way `AuthService` normalizes it, so
    ``A@X.com`` and ``a@x.com`` share one window rather than doubling the
    allowance. A caller with no `Origin` header (every non-browser client)
    shares the one bucket for that email, which is the account-wide limit.
    """

    return f"{email.strip().lower()}|{origin or '-'}"


class LoginThrottle:
    """Sliding-window counter of recent failed logins, keyed by email+origin."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        # Monotonic: a clock adjustment must not widen or void a window.
        self._clock = clock or time.monotonic
        self._failures: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        """Raise if ``key`` has already spent its allowance this window."""

        attempts = self._live(key)
        if attempts is not None and len(attempts) >= self._max_attempts:
            raise TooManyLoginAttemptsError()

    def record_failure(self, key: str) -> None:
        """Count one failed login against ``key``."""

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
        """Forget ``key``'s failures -- called on a successful login."""

        self._failures.pop(key, None)

    # -- internals ----------------------------------------------------------

    def _live(self, key: str) -> deque[float] | None:
        """Return ``key``'s failures still inside the window, or None when it
        has none left. Drops the key entirely once its window drains, which is
        what keeps idle keys from accumulating."""

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
