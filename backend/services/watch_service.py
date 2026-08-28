"""Watch lifecycle and the polling routine the background queue runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import UTC, datetime, timedelta
from enum import Enum
import logging
import random
from typing import Any, List
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.config import (
    DEFAULT_MAX_POLL_ATTEMPTS,
    DEFAULT_PROVIDER_BACKOFF_MAX_SECONDS,
    DEFAULT_PROVIDER_CALL_TIMEOUT_SECONDS,
)
from backend.db.repositories.watch_decisions import (
    BookingPermitStatus,
    ClaimStatus,
    CommitStatus,
    TransitionStatus,
    WindowClaim,
)
from backend.integrations.base import (
    AdapterError,
    ProviderSequenceTimeout,
    ReservationAdapter,
    SlotNotFoundError,
    SlotUnavailableError,
)
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import (
    Watch,
    WatchPollOutcome,
    WatchPollResult,
    WatchStatus,
    default_expiry,
)
from backend.models.watch_runtime import initial_runtime, window_id_for
from backend.orchestrator.schemas import ReservationIntent
from backend.services.notification_service import (
    LoggingNotificationService,
    NotificationService,
    WatchEvent,
)
from backend.services.watch_policy import (
    AvailabilityPolicy,
    AvailabilityPolicyFactory,
)
from backend.workers.scheduler import MIN_DELAY_SECONDS, PollSchedule


logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

#: Slots stored on a watch, so a long-lived FOUND record stays a sane size.
MAX_RECORDED_SLOTS = 16

#: How long a poll owns its cadence window. It exceeds the worker's hard task
#: limit, so no other owner can take over while the original task can still be
#: running. Wired to a dedicated setting when timing validation lands.
_POLL_LEASE_SECONDS = 120.0


class WatchService:
    """Creates watches and performs the single availability check a job runs.

    `poll_once` is the queue handler. It is a plain coroutine with no Celery or
    Redis import of its own: the worker task is a thin wrapper around it, which
    is what lets the whole polling contract be tested without a broker.

    Polling is claim-first: `poll_window` takes a fenced, expiring claim on one
    cadence window before any provider call, and every transition is one atomic
    `commit_window`. A duplicate delivery of a window that is already owned,
    stale, or finished does no provider work and reports `ALREADY_FINISHED`.
    """

    def __init__(
        self,
        repository: Any,
        adapter: ReservationAdapter,
        queue: Any,
        *,
        schedule: PollSchedule | None = None,
        notifier: NotificationService | None = None,
        max_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
        provider_timeout_seconds: float = DEFAULT_PROVIDER_CALL_TIMEOUT_SECONDS,
        backoff_max_seconds: float = DEFAULT_PROVIDER_BACKOFF_MAX_SECONDS,
        clock: Clock | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._queue = queue
        self._schedule = schedule or PollSchedule()
        self._notifier = notifier or LoggingNotificationService()
        self._max_attempts = max_attempts
        self._provider_timeout_seconds = provider_timeout_seconds
        self._backoff_max_seconds = max(
            backoff_max_seconds, self._schedule.interval_seconds
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reservation_timezone = (
            ZoneInfo(timezone_name) if timezone_name is not None else None
        )
        self._policy_factory = AvailabilityPolicyFactory(self._schedule)

    async def create_from_intent(
        self,
        intent: ReservationIntent,
        *,
        auto_book: bool = False,
    ) -> Watch:
        """Open a watch for a validated intent that asked to be monitored."""

        if not intent.is_ready:
            raise ValueError("cannot watch an intent that still needs clarification")
        return await self.create(self._query_from(intent), auto_book=auto_book)

    async def create(
        self,
        query: AvailabilityQuery,
        *,
        auto_book: bool = False,
    ) -> Watch:
        """Persist a new watch and dispatch its first check immediately.

        The watch, its runtime sidecar, and its first durable schedule marker
        commit together; the immediate queue publication is best-effort on top
        of that durable marker, so a broker outage still returns the created
        watch and leaves the marker for recovery to dispatch.
        """

        now = self._clock()
        timezone = self._reservation_timezone or now.tzinfo or UTC
        expires_at = default_expiry(query, now, timezone)

        # The stored ceiling is the derived, lifetime-aware budget: enough
        # checks to reach the deadline, or the operator's smaller ceiling.
        policy = self._policy_factory.derive(
            now=now,
            expires_at=expires_at,
            safety_ceiling=self._max_attempts,
        )

        watch = Watch(
            watch_id=f"watch_{uuid4().hex}",
            status=WatchStatus.ACTIVE,
            query=query,
            auto_book=auto_book,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            attempts=0,
            max_attempts=policy.effective_attempts,
            next_check_at=now,
        )
        runtime = initial_runtime(
            watch,
            required_attempts=policy.required_attempts,
            supports_deadline=policy.supports_deadline,
        )
        result = await self._repository.create_with_schedule(watch, runtime)
        stored = result.watch or watch

        # The first check runs without jitter: the user just asked, so the
        # latency they see is the one that matters. Jitter starts on retries.
        await self._dispatch(
            stored.watch_id, runtime.window_id, delay_seconds=0.0, due_at=now
        )
        return stored

    def describe_policy(self, watch: Watch) -> AvailabilityPolicy:
        """Recover a watch's monitoring policy for messaging surfaces.

        The stored `max_attempts` is already the effective budget, so deriving
        against it as the ceiling reproduces the creation-time policy without
        adding any field to the public `Watch` JSON.
        """

        return self._policy_factory.derive(
            now=watch.created_at,
            expires_at=watch.expires_at,
            safety_ceiling=watch.max_attempts,
        )

    async def get(self, watch_id: str) -> Watch | None:
        return await self._repository.get(watch_id)

    async def list(self, *, active_only: bool = False) -> list[Watch]:
        if active_only:
            return await self._repository.list_active()
        return await self._repository.list_all()

    async def cancel(self, watch_id: str) -> Watch | None:
        """Stop a watch, fencing any in-flight claim against it."""

        result = await self._repository.cancel_if_active(watch_id)
        if result.status is TransitionStatus.UNKNOWN:
            return None
        # APPLIED -> the CANCELLED watch; NOOP -> the already-terminal watch;
        # NOT_ELIGIBLE -> a booking is in flight, so the request is recorded and
        # the still-active watch is returned (its resolution is the owner's).
        return result.watch

    # -- polling ------------------------------------------------------------

    async def poll_once(self, watch_id: str) -> WatchPollResult:
        """Run one availability check on a watch's current cadence window.

        This is the compatibility entry point for one-argument queue jobs: it
        resolves the repository-authoritative current window and polls it as
        due. A record with no current window (a pre-sidecar document, or one
        reactivated outside the protocol) falls back to the legacy path.
        """

        runtime = await self._repository.get_runtime(watch_id)
        if runtime is not None and runtime.window_id is not None:
            return await self.poll_window(
                watch_id, runtime.window_id, enforce_due=False
            )

        watch = await self._repository.get(watch_id)
        if watch is None:
            return WatchPollResult(
                outcome=WatchPollOutcome.UNKNOWN_WATCH, watch=None
            )
        if watch.status.is_terminal:
            return WatchPollResult(
                outcome=WatchPollOutcome.ALREADY_FINISHED, watch=watch
            )
        return await self._legacy_poll_once(watch)

    async def poll_window(
        self,
        watch_id: str,
        window_id: str,
        *,
        owner_id: str | None = None,
        enforce_due: bool = True,
    ) -> WatchPollResult:
        """Claim one cadence window and run its single availability check.

        `enforce_due` keeps the not-yet-due guard for window-aware deliveries;
        the legacy `poll_once` path clears it because the job's arrival is the
        authority that the window is due.
        """

        owner = owner_id or uuid4().hex
        claim_result = await self._repository.claim_window(
            watch_id,
            window_id,
            owner,
            _POLL_LEASE_SECONDS,
            ignore_schedule=not enforce_due,
        )
        status = claim_result.status
        if status is ClaimStatus.UNKNOWN:
            return WatchPollResult(
                outcome=WatchPollOutcome.UNKNOWN_WATCH, watch=None
            )
        if status is not ClaimStatus.OWNED:
            # TERMINAL, BUSY, EARLY, or STALE: this delivery has no work. The
            # persisted watch stays the authority for overall terminality.
            return self._noop(await self._repository.get(watch_id))

        assert claim_result.claim is not None
        return await self._run_claimed(claim_result.claim)

    async def _run_claimed(self, claim: WindowClaim) -> WatchPollResult:
        now = self._clock()
        watch = claim.watch

        if now >= watch.expires_at or watch.attempts >= watch.max_attempts:
            return await self._commit_terminal(
                claim, self._expired_watch(watch, now, attempted=False)
            )

        try:
            async with asyncio.timeout(self._provider_timeout_seconds):
                kind, payload = await self._provider_sequence(claim, watch)
        except TimeoutError:
            # Only this owned deadline is an outage. Outer CancelledError and
            # Celery soft/hard time limits are deliberately not caught here.
            return await self._commit_outage(
                claim, watch, now, error="The reservation provider timed out"
            )
        except (SlotUnavailableError, SlotNotFoundError):
            # An expected slot race is a normal booking miss, never an
            # infrastructure outage. (These are handled inside `_book`; caught
            # here only if one escapes a future path.)
            return await self._commit_miss(claim, watch, now, error=None)
        except AdapterError as exc:
            return await self._commit_outage(
                claim,
                watch,
                now,
                error=str(exc)[:500] or "The reservation provider failed",
            )

        if kind is _Sequence.MISS:
            return await self._commit_miss(claim, watch, now, error=None)
        if kind is _Sequence.FOUND:
            return await self._commit_terminal(
                claim, self._found_watch(watch, payload, now)
            )
        if kind is _Sequence.BOOKED:
            return await self._commit_terminal(
                claim, self._booked_watch(watch, now, payload, attempted=True)
            )
        if kind is _Sequence.BOOKED_REPLAY:
            return await self._commit_terminal(
                claim, self._booked_watch(watch, now, payload, attempted=False)
            )
        # NOOP: a cancellation or a fence beat the booking-permit linearization.
        await self._repository.release_claim(claim)
        return self._noop(await self._repository.get(watch.watch_id))

    async def _provider_sequence(
        self, claim: WindowClaim, watch: Watch
    ) -> tuple[_Sequence, Any]:
        """The bounded provider interaction, run inside one owned timeout.

        It performs only provider and booking-permit work and returns a plan;
        the atomic commit happens outside the deadline, so a slow commit is
        never misread as a provider outage. A generic `AdapterError` propagates
        to the outage path, while an expected slot race resolves to a normal
        miss inside `_book`.
        """

        key = self._idempotency_key(watch)
        if watch.auto_book:
            existing = await self._adapter.get_booking(key)
            if existing is not None:
                # A prior delivery of this window booked before it could commit.
                return _Sequence.BOOKED_REPLAY, existing

        slots = await self._adapter.search_availability(watch.query)
        if not slots:
            return _Sequence.MISS, None
        if not watch.auto_book:
            return _Sequence.FOUND, slots

        permit = await self._repository.begin_booking(claim)
        if permit.status is not BookingPermitStatus.GRANTED:
            return _Sequence.NOOP, None
        confirmation = await self._book(watch, slots)
        if confirmation is None:
            return _Sequence.MISS, None
        return _Sequence.BOOKED, confirmation

    async def _commit_outage(
        self,
        claim: WindowClaim,
        watch: Watch,
        now: datetime,
        *,
        error: str,
    ) -> WatchPollResult:
        """Commit a provider outage: no attempt, capped exponential backoff."""

        outages = self._saturating_outages(claim.runtime.consecutive_outages + 1)
        delay = self._outage_delay(outages, now, watch)
        if delay is None:
            # Less than the anti-hot-poll floor of lifetime remains; finish now
            # rather than schedule a check that cannot run in time.
            return await self._commit_terminal(
                claim, self._expired_watch(watch, now, attempted=False)
            )

        scheduled = now + timedelta(seconds=delay)
        cadence = claim.runtime.cadence_sequence + 1
        outage_watch = watch.model_copy(
            update={
                # No attempt is consumed: the user's availability budget is not
                # spent on the provider being unreachable.
                "last_checked_at": now,
                "updated_at": now,
                "last_error": error,
                "next_check_at": scheduled,
            }
        )
        outage_runtime = claim.runtime.model_copy(
            update={
                "cadence_sequence": cadence,
                "window_id": window_id_for(watch.watch_id, cadence),
                "scheduled_for": scheduled,
                "phase": None,
                "cancel_requested": False,
                "consecutive_outages": outages,
            }
        )
        result = await self._commit_window(claim, outage_watch, outage_runtime)
        if result.status is not CommitStatus.COMMITTED:
            return self._noop(await self._repository.get(watch.watch_id))

        committed = result.watch or outage_watch
        await self._dispatch(
            committed.watch_id,
            outage_runtime.window_id,
            delay_seconds=delay,
            due_at=scheduled,
        )
        return WatchPollResult(
            outcome=WatchPollOutcome.NO_AVAILABILITY,
            watch=committed,
            retry_in_seconds=delay,
        )

    def _outage_delay(
        self, outages: int, now: datetime, watch: Watch
    ) -> float | None:
        """`interval * 2**(n-1)` capped, jittered, floored, and deadline-clipped.

        Returns None when less than the anti-hot-poll floor of lifetime remains,
        which the caller turns into an immediate `EXPIRED` with no provider call.
        """

        interval = self._schedule.interval_seconds
        jitter = self._schedule.jitter_seconds
        maximum = self._backoff_max_seconds
        exponent_limit = self._exponent_limit(interval, maximum)
        if outages - 1 >= exponent_limit:
            base = maximum
        else:
            base = interval * (2 ** (outages - 1))
        jittered = base + random.uniform(-jitter, jitter)
        jittered = min(max(jittered, MIN_DELAY_SECONDS), maximum)

        remaining = (watch.expires_at - now).total_seconds()
        if remaining < MIN_DELAY_SECONDS:
            return None
        return min(jittered, remaining)

    def _saturating_outages(self, value: int) -> int:
        # Cap the stored counter one past the exponent limit: beyond that the
        # delay is already the maximum, so a larger count carries no meaning and
        # a corrupt value can never drive an unbounded exponentiation.
        cap = (
            self._exponent_limit(
                self._schedule.interval_seconds, self._backoff_max_seconds
            )
            + 1
        )
        return min(value, cap)

    @staticmethod
    def _exponent_limit(interval: float, maximum: float) -> int:
        """Smallest k with `interval * 2**k >= maximum` (a bounded ceil-log2)."""

        limit = 0
        value = interval
        while value < maximum:
            value *= 2
            limit += 1
        return limit

    async def _commit_miss(
        self,
        claim: WindowClaim,
        watch: Watch,
        now: datetime,
        *,
        error: str | None,
    ) -> WatchPollResult:
        """Commit one availability miss and schedule at most one successor."""

        attempts = min(watch.attempts + 1, watch.max_attempts)
        attempted = watch.model_copy(
            update={
                "attempts": attempts,
                "last_checked_at": now,
                "updated_at": now,
                "last_error": error,
            }
        )

        remaining_seconds = (watch.expires_at - now).total_seconds()
        delay = min(self._schedule.next_delay(), remaining_seconds)
        # Reaching the ceiling, or having less than the anti-hot-poll floor of
        # lifetime left, finishes the watch on this same commit.
        if attempts >= watch.max_attempts or delay < MIN_DELAY_SECONDS:
            return await self._commit_terminal(
                claim, self._expired_watch(attempted, now, attempted=True)
            )

        scheduled = now + timedelta(seconds=delay)
        cadence = claim.runtime.cadence_sequence + 1
        successor = attempted.model_copy(update={"next_check_at": scheduled})
        successor_runtime = claim.runtime.model_copy(
            update={
                "cadence_sequence": cadence,
                "window_id": window_id_for(watch.watch_id, cadence),
                "scheduled_for": scheduled,
                "phase": None,
                "cancel_requested": False,
                "consecutive_outages": 0,
            }
        )
        result = await self._commit_window(claim, successor, successor_runtime)
        if result.status is not CommitStatus.COMMITTED:
            return self._noop(await self._repository.get(watch.watch_id))

        committed = result.watch or successor
        await self._dispatch(
            committed.watch_id,
            successor_runtime.window_id,
            delay_seconds=delay,
            due_at=scheduled,
        )
        return WatchPollResult(
            outcome=WatchPollOutcome.NO_AVAILABILITY,
            watch=committed,
            retry_in_seconds=delay,
        )

    async def _commit_terminal(
        self,
        claim: WindowClaim,
        new_watch: Watch,
    ) -> WatchPollResult:
        """Commit a terminal transition and notify at most once."""

        terminal_runtime = claim.runtime.model_copy(
            update={
                "window_id": None,
                "scheduled_for": None,
                "phase": None,
                "cancel_requested": False,
                "consecutive_outages": 0,
            }
        )
        result = await self._commit_window(claim, new_watch, terminal_runtime)
        if result.status is not CommitStatus.COMMITTED:
            return self._noop(await self._repository.get(new_watch.watch_id))

        committed = result.watch or new_watch
        # A terminal event id is issued at most once per transition, so gating
        # the notification on it makes delivery observably at-most-once.
        if result.event_id is not None:
            await self._notifier.notify(committed, _EVENT_FOR[committed.status])
        return WatchPollResult(
            outcome=_OUTCOME_FOR[committed.status], watch=committed
        )

    async def _commit_window(
        self,
        claim: WindowClaim,
        new_watch: Watch,
        new_runtime: Any,
    ) -> Any:
        """Run the one atomic commit, shielded from outer cancellation.

        If the surrounding task is cancelled mid-commit -- a Celery soft/hard
        time limit -- the commit still runs to a known result before the
        cancellation propagates, so a watch is never left half-transitioned.
        Redis applies the underlying script all-or-nothing regardless.
        """

        task = asyncio.ensure_future(
            self._repository.commit_window(claim, new_watch, new_runtime)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with contextlib.suppress(BaseException):
                await task
            raise

    def _noop(self, watch: Watch | None) -> WatchPollResult:
        """A delivery with no remaining work for this window."""

        if watch is None:
            return WatchPollResult(
                outcome=WatchPollOutcome.UNKNOWN_WATCH, watch=None
            )
        return WatchPollResult(
            outcome=WatchPollOutcome.ALREADY_FINISHED, watch=watch
        )

    # -- terminal watch builders --------------------------------------------

    def _found_watch(
        self, watch: Watch, slots: List[Any], now: datetime
    ) -> Watch:
        return watch.model_copy(
            update={
                "status": WatchStatus.FOUND,
                "found_slots": slots[:MAX_RECORDED_SLOTS],
                "next_check_at": None,
                "updated_at": now,
                "attempts": min(watch.attempts + 1, watch.max_attempts),
                "last_checked_at": now,
                "last_error": None,
            }
        )

    def _booked_watch(
        self,
        watch: Watch,
        now: datetime,
        confirmation: Any,
        *,
        attempted: bool,
    ) -> Watch:
        update: dict[str, Any] = {
            "status": WatchStatus.BOOKED,
            "found_slots": [confirmation.slot],
            "booking": confirmation,
            "next_check_at": None,
            "updated_at": now,
        }
        if attempted:
            update["attempts"] = min(watch.attempts + 1, watch.max_attempts)
            update["last_checked_at"] = now
            update["last_error"] = None
        return watch.model_copy(update=update)

    def _expired_watch(
        self, watch: Watch, now: datetime, *, attempted: bool
    ) -> Watch:
        update: dict[str, Any] = {
            "status": WatchStatus.EXPIRED,
            "next_check_at": None,
            "updated_at": now,
        }
        if attempted:
            update["last_checked_at"] = now
        return watch.model_copy(update=update)

    # -- queue publication --------------------------------------------------

    async def _dispatch(
        self,
        watch_id: str,
        window_id: str | None,
        *,
        delay_seconds: float,
        due_at: datetime,
    ) -> None:
        """Best-effort publication; the durable marker is the authority."""

        try:
            await self._queue.enqueue_watch_poll(
                watch_id,
                window_id=window_id,
                delay_seconds=delay_seconds,
                due_at=due_at,
            )
        except Exception:
            logger.warning(
                "queue publication failed for watch %s; the durable marker "
                "remains for recovery to dispatch",
                watch_id,
            )

    # -- provider interaction (shared by both paths) ------------------------

    async def _search(self, watch: Watch) -> tuple[List[Any], str | None]:
        """Check availability, turning adapter failures into a retryable miss."""

        try:
            slots = await self._adapter.search_availability(watch.query)
        except AdapterError as exc:
            return [], str(exc)[:500] or "The reservation provider failed"
        return slots, None

    async def _book(self, watch: Watch, slots: List[Any]) -> Any | None:
        """Book the first slot that remains available."""

        for slot in slots:
            try:
                return await self._adapter.book_slot(
                    slot.slot_id,
                    idempotency_key=self._idempotency_key(watch),
                )
            except (SlotUnavailableError, SlotNotFoundError):
                continue
        return None

    @staticmethod
    def _idempotency_key(watch: Watch) -> str:
        """One watch is one reservation attempt, however often it is polled."""

        return f"watch:{watch.watch_id}"

    @staticmethod
    def _query_from(intent: ReservationIntent) -> AvailabilityQuery:
        if (
            intent.venue_name is None
            or intent.party_size is None
            or intent.date is None
        ):
            raise ValueError("ready intent is missing required watch parameters")

        return AvailabilityQuery(
            venue_name=intent.venue_name,
            venue_type=intent.venue_type,
            market=intent.market,
            party_size=intent.party_size,
            date=intent.date,
            preferred_time=intent.preferred_time,
            time_window=intent.time_window,
            duration_minutes=intent.duration_minutes,
            special_requests=intent.special_requests,
        )

    # -- legacy compatibility path ------------------------------------------
    #
    # Used only for a record without a current cadence window: a pre-sidecar
    # document, or a watch reactivated outside the coordinated protocol. It is
    # the pre-atomic snapshot routine and is removed once legacy migration is
    # wired into the read path.

    async def _legacy_poll_once(self, watch: Watch) -> WatchPollResult:
        now = self._clock()
        if watch.is_exhausted(now):
            return await self._legacy_expire(watch, now)

        replayed = await self._legacy_replayed_booking(watch, now)
        if replayed is not None:
            return replayed

        slots, error = await self._search(watch)
        attempted = watch.model_copy(
            update={
                "attempts": min(watch.attempts + 1, watch.max_attempts),
                "last_checked_at": now,
                "updated_at": now,
                "last_error": error,
            }
        )

        if slots:
            return await self._legacy_fulfill(attempted, slots, now)
        if attempted.is_exhausted(now):
            return await self._legacy_expire(attempted, now)
        return await self._legacy_reschedule(attempted, now)

    async def _legacy_fulfill(
        self, watch: Watch, slots: List[Any], now: datetime
    ) -> WatchPollResult:
        recorded = slots[:MAX_RECORDED_SLOTS]
        if watch.auto_book:
            try:
                confirmation = await self._book(watch, slots)
            except AdapterError as exc:
                return await self._legacy_retry_auto_book(
                    watch, now, str(exc)[:500] or "The reservation provider failed"
                )
            if confirmation is None:
                return await self._legacy_retry_auto_book(
                    watch,
                    now,
                    "Available slots disappeared before they could be booked",
                )
            booked = await self._repository.save(
                watch.model_copy(
                    update={
                        "status": WatchStatus.BOOKED,
                        "found_slots": [confirmation.slot],
                        "booking": confirmation,
                        "next_check_at": None,
                        "updated_at": now,
                    }
                )
            )
            await self._notifier.notify(booked, WatchEvent.BOOKED)
            return WatchPollResult(outcome=WatchPollOutcome.BOOKED, watch=booked)

        found = await self._repository.save(
            watch.model_copy(
                update={
                    "status": WatchStatus.FOUND,
                    "found_slots": recorded,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
        )
        await self._notifier.notify(found, WatchEvent.AVAILABILITY_FOUND)
        return WatchPollResult(outcome=WatchPollOutcome.FOUND, watch=found)

    async def _legacy_retry_auto_book(
        self, watch: Watch, now: datetime, error: str
    ) -> WatchPollResult:
        retrying = watch.model_copy(update={"last_error": error})
        if retrying.is_exhausted(now):
            return await self._legacy_expire(retrying, now)
        return await self._legacy_reschedule(retrying, now)

    async def _legacy_replayed_booking(
        self, watch: Watch, now: datetime
    ) -> WatchPollResult | None:
        if not watch.auto_book:
            return None
        existing = await self._adapter.get_booking(self._idempotency_key(watch))
        if existing is None:
            return None
        booked = await self._repository.save(
            watch.model_copy(
                update={
                    "status": WatchStatus.BOOKED,
                    "found_slots": [existing.slot],
                    "booking": existing,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
        )
        await self._notifier.notify(booked, WatchEvent.BOOKED)
        return WatchPollResult(outcome=WatchPollOutcome.BOOKED, watch=booked)

    async def _legacy_reschedule(
        self, watch: Watch, now: datetime
    ) -> WatchPollResult:
        remaining_seconds = (watch.expires_at - now).total_seconds()
        if remaining_seconds <= 0:
            return await self._legacy_expire(watch, now)
        delay = min(self._schedule.next_delay(), remaining_seconds)
        pending = await self._repository.save(
            watch.model_copy(
                update={
                    "next_check_at": now + timedelta(seconds=delay),
                    "updated_at": now,
                }
            )
        )
        await self._queue.enqueue_watch_poll(watch.watch_id, delay_seconds=delay)
        return WatchPollResult(
            outcome=WatchPollOutcome.NO_AVAILABILITY,
            watch=pending,
            retry_in_seconds=delay,
        )

    async def _legacy_expire(self, watch: Watch, now: datetime) -> WatchPollResult:
        expired = await self._repository.save(
            watch.model_copy(
                update={
                    "status": WatchStatus.EXPIRED,
                    "next_check_at": None,
                    "updated_at": now,
                }
            )
        )
        await self._notifier.notify(expired, WatchEvent.EXPIRED)
        return WatchPollResult(outcome=WatchPollOutcome.EXPIRED, watch=expired)


class _Sequence(Enum):
    """The outcome of the bounded provider sequence, decided before committing."""

    MISS = "miss"
    FOUND = "found"
    BOOKED = "booked"
    BOOKED_REPLAY = "booked_replay"
    NOOP = "noop"


_EVENT_FOR = {
    WatchStatus.FOUND: WatchEvent.AVAILABILITY_FOUND,
    WatchStatus.BOOKED: WatchEvent.BOOKED,
    WatchStatus.EXPIRED: WatchEvent.EXPIRED,
}

_OUTCOME_FOR = {
    WatchStatus.FOUND: WatchPollOutcome.FOUND,
    WatchStatus.BOOKED: WatchPollOutcome.BOOKED,
    WatchStatus.EXPIRED: WatchPollOutcome.EXPIRED,
}
