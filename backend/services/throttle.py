"""Best-effort sliding-window throttling (-> 429).

One in-process counter, used for two things:

- **Failed logins** (Milestone 5, Req 6.4): only failures count and a success
  clears the window, so a user who mistypes a password and then gets it right is
  never penalized, and a third party hammering someone else's email cannot lock
  that account out for more than the short window.
- **The paid prompt endpoint** (Milestone 6): every request counts, because the
  cost is incurred whether or not the call succeeds.

Two implementations with one async interface (`acheck`/`arecord`/`areset`):

- `SlidingWindowThrottle` is in-process. It is the default, and what stays in
  use when Redis is unavailable -- then N processes each allow the threshold.
  It fails open past `_MAX_TRACKED_KEYS` live keys, so an attacker cycling keys
  can never turn the throttle itself into a memory-exhaustion vector.
- `RedisSlidingWindowThrottle` keeps the window in Redis, so every API process
  shares one limit. The app switches to it once Redis connects. A Redis error
  fails open (logged) rather than turning a limiter outage into an API outage.
"""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)

__all__ = [
    "RateLimitedError",
    "RedisSlidingWindowThrottle",
    "SlidingWindowThrottle",
    "TooManyLoginAttemptsError",
    "is_internal_address",
    "login_email_key",
    "login_ip_key",
    "throttle_key",
]


class RateLimitedError(Exception):
    """Too many events for this key inside the window (-> 429)."""


class TooManyLoginAttemptsError(RateLimitedError):
    """Too many recent failed logins for this email or address (-> 429)."""

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


def login_email_key(email: str) -> str:
    """Bucket failed logins by account email alone.

    Never by `Origin`: a script can send any `Origin` it likes, so keying on it
    would hand out a fresh allowance per made-up value.
    """

    return f"email:{email.strip().lower()}"


#: Carrier-grade NAT space. Some hosts route proxy traffic through it, and
#: Python does not count it as private.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def is_internal_address(host: str | None) -> bool:
    """True for loopback, private, or shared-NAT addresses.

    A login arriving from one of these is almost always a hosting proxy whose
    real-client header is not trusted (FORWARDED_ALLOW_IPS unset), so every
    user appears to share it. Callers skip the per-address limit then, or one
    attacker could lock every user out. An internet client cannot fake this:
    the address is the TCP peer, not a header.
    """

    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback or address.is_private:
        return True
    return address.version == 4 and address in _SHARED_ADDRESS_SPACE


def login_ip_key(peer: str | None) -> str:
    """Bucket failed logins by client address, across every email tried."""

    return f"ip:{peer or '-'}"


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
        self._failures: dict[str, deque[tuple[float, str]]] = {}

    def check(self, key: str) -> None:
        """Raise if ``key`` has already spent its allowance this window."""

        attempts = self._live(key)
        if attempts is not None and len(attempts) >= self._max_events:
            raise self._on_limit()

    def record(self, key: str) -> str | None:
        """Count one event against ``key``."""

        attempts = self._live(key)
        if attempts is None:
            if len(self._failures) >= _MAX_TRACKED_KEYS:
                self._sweep()
            if len(self._failures) >= _MAX_TRACKED_KEYS:
                return None  # fail open rather than grow without bound
            attempts = deque()
            self._failures[key] = attempts
        token = uuid.uuid4().hex
        attempts.append((self._clock(), token))
        return token

    def reset(self, key: str) -> None:
        """Forget ``key``'s events -- used on a successful login, so a fumbled
        password that is then corrected costs nothing. Not used by quota-style
        callers, where every event counts."""

        self._failures.pop(key, None)

    # -- async interface, shared with RedisSlidingWindowThrottle ------------

    async def acheck(self, key: str) -> None:
        self.check(key)

    async def arecord(self, key: str) -> None:
        self.record(key)

    async def areset(self, key: str) -> None:
        self.reset(key)

    async def aconsume(self, key: str) -> str | None:
        """Check and reserve one event without yielding between operations."""
        self.check(key)
        return self.record(key)

    async def arelease(self, key: str, token: str | None) -> None:
        """Remove just this successful/aborted request's reservation."""
        attempts = self._live(key)
        if attempts is not None and token is not None:
            for event in attempts:
                if event[1] == token:
                    attempts.remove(event)
                    break

    # -- internals ----------------------------------------------------------

    def _live(self, key: str) -> deque[tuple[float, str]] | None:
        """Return ``key``'s events still inside the window, or None when it has
        none left. Drops the key entirely once its window drains, which is what
        keeps idle keys from accumulating."""

        attempts = self._failures.get(key)
        if attempts is None:
            return None
        cutoff = self._clock() - self._window_seconds
        while attempts and attempts[0][0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def _sweep(self) -> None:
        """Drop every key whose window has fully drained."""

        for key in list(self._failures):
            self._live(key)


_CONSUME_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


class RedisSlidingWindowThrottle:
    """The same sliding window, stored in Redis so every process shares it.

    Each key is a sorted set of event timestamps that expires with its window,
    so idle keys clean themselves up. Timestamps are wall-clock (shared across
    hosts), not monotonic.
    """

    def __init__(
        self,
        client: Any,
        *,
        name: str,
        max_events: int,
        window_seconds: int,
        on_limit: Callable[[], RateLimitedError] = RateLimitedError,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._client = client
        self._prefix = f"dibs:throttle:{name}:"
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._on_limit = on_limit
        self._clock = clock or time.time

    @classmethod
    def like(
        cls, local: SlidingWindowThrottle, client: Any, *, name: str
    ) -> RedisSlidingWindowThrottle:
        """A Redis throttle with the same limit as an in-process one."""

        return cls(
            client,
            name=name,
            max_events=local._max_events,
            window_seconds=local._window_seconds,
            on_limit=local._on_limit,
        )

    async def acheck(self, key: str) -> None:
        cutoff = self._clock() - self._window_seconds
        try:
            pipe = self._client.pipeline(transaction=True)
            pipe.zremrangebyscore(self._prefix + key, "-inf", cutoff)
            pipe.zcard(self._prefix + key)
            _, count = await pipe.execute()
        except Exception:
            logger.warning("throttle check failed; allowing request", exc_info=True)
            return
        if int(count) >= self._max_events:
            raise self._on_limit()

    async def arecord(self, key: str) -> None:
        now = self._clock()
        try:
            pipe = self._client.pipeline(transaction=True)
            pipe.zadd(self._prefix + key, {f"{now:.6f}:{uuid.uuid4().hex}": now})
            pipe.expire(self._prefix + key, self._window_seconds + 1)
            await pipe.execute()
        except Exception:
            logger.warning("throttle record failed; event not counted", exc_info=True)

    async def areset(self, key: str) -> None:
        try:
            await self._client.delete(self._prefix + key)
        except Exception:
            logger.warning("throttle reset failed", exc_info=True)

    async def aconsume(self, key: str) -> str | None:
        """Reserve quota atomically across all API processes."""
        now = self._clock()
        token = uuid.uuid4().hex
        try:
            accepted = await self._client.eval(
                _CONSUME_LUA, 1, self._prefix + key,
                now - self._window_seconds, self._max_events, now,
                token, self._window_seconds + 1,
            )
        except Exception:
            logger.warning("throttle admission failed; allowing request", exc_info=True)
            return None
        if not accepted:
            raise self._on_limit()
        return token

    async def arelease(self, key: str, token: str | None) -> None:
        if token is None:
            return
        try:
            await self._client.zrem(self._prefix + key, token)
        except Exception:
            logger.warning("throttle release failed", exc_info=True)
