"""Watch lifecycle and the polling routine the background queue runs."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.config import DEFAULT_MAX_POLL_ATTEMPTS
from backend.integrations.base import (
    AdapterError,
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
from backend.orchestrator.schemas import ReservationIntent
from backend.services.notification_service import (
    LoggingNotificationService,
    NotificationService,
    WatchEvent,
)
from backend.workers.scheduler import PollSchedule


Clock = Callable[[], datetime]

#: Slots stored on a watch, so a long-lived FOUND record stays a sane size.
MAX_RECORDED_SLOTS = 16


class WatchService:
    """Creates watches and performs the single availability check a job runs.

    `poll_once` is the queue handler. It is a plain coroutine with no Celery or
    Redis import of its own: the worker task is a thin wrapper around it, which
    is what lets the whole polling contract be tested without a broker.
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
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._queue = queue
        self._schedule = schedule or PollSchedule()
        self._notifier = notifier or LoggingNotificationService()
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))

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
        """Persist a new watch and dispatch its first check immediately."""

        now = self._clock()
        watch = Watch(
            watch_id=f"watch_{uuid4().hex}",
            status=WatchStatus.ACTIVE,
            query=query,
            auto_book=auto_book,
            created_at=now,
            updated_at=now,
            expires_at=default_expiry(query, now),
            attempts=0,
            max_attempts=self._max_attempts,
            next_check_at=now,
        )
        await self._repository.save(watch)
        # The first check runs without jitter: the user just asked, so the
        # latency they see is the one that matters. Jitter starts on retries.
        await self._queue.enqueue_watch_poll(watch.watch_id, delay_seconds=0.0)
        return watch

    async def get(self, watch_id: str) -> Watch | None:
        return await self._repository.get(watch_id)

    async def list(self, *, active_only: bool = False) -> list[Watch]:
        if active_only:
            return await self._repository.list_active()
        return await self._repository.list_all()

    async def cancel(self, watch_id: str) -> Watch | None:
        """Stop a watch. The next queued poll sees the status and exits."""

        watch = await self._repository.get(watch_id)
        if watch is None:
            return None
        if watch.status.is_terminal:
            return watch
        return await self._repository.save(
            watch.model_copy(
                update={
                    "status": WatchStatus.CANCELLED,
                    "updated_at": self._clock(),
                    "next_check_at": None,
                }
            )
        )

    async def poll_once(self, watch_id: str) -> WatchPollResult:
        """Run one availability check and decide what happens next.

        This is the queue handler. Every exit path either finishes the watch or
        schedules exactly one successor job, so a watch can never fan out into
        multiple concurrent polling chains.
        """

        watch = await self._repository.get(watch_id)
        if watch is None:
            return WatchPollResult(
                outcome=WatchPollOutcome.UNKNOWN_WATCH,
                watch=None,
            )
        if watch.status.is_terminal:
            return WatchPollResult(
                outcome=WatchPollOutcome.ALREADY_FINISHED,
                watch=watch,
            )

        now = self._clock()
        if watch.is_exhausted(now):
            return await self._expire(watch, now)

        replayed = await self._replayed_booking(watch, now)
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
            return await self._fulfill(attempted, slots, now)
        if attempted.is_exhausted(now):
            return await self._expire(attempted, now)
        return await self._reschedule(attempted, now)

    async def _search(self, watch: Watch) -> tuple[list[Any], str | None]:
        """Check availability, turning adapter failures into a retryable miss.

        A provider outage is temporary; killing the watch over it would lose
        the reservation the user actually wanted. The error is recorded on the
        watch so it stays visible.
        """

        try:
            slots = await self._adapter.search_availability(watch.query)
        except AdapterError as exc:
            return [], str(exc)[:500] or "The reservation provider failed"
        return slots, None

    async def _fulfill(
        self,
        watch: Watch,
        slots: list[Any],
        now: datetime,
    ) -> WatchPollResult:
        """Record found availability, booking it when the watch asked to."""

        recorded = slots[:MAX_RECORDED_SLOTS]
        if watch.auto_book:
            confirmation = await self._book(watch, slots)
            if confirmation is not None:
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

    async def _replayed_booking(
        self,
        watch: Watch,
        now: datetime,
    ) -> WatchPollResult | None:
        """Recover a reservation an earlier delivery of this job already made.

        A broker that redelivers after the booking succeeded but before the
        watch was saved would otherwise re-search, find the slot it just took
        marked unavailable, and keep polling for a table it already holds.
        """

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
        return WatchPollResult(outcome=WatchPollOutcome.BOOKED, watch=booked)

    async def _book(self, watch: Watch, slots: list[Any]) -> Any | None:
        """Take the first slot that is still there when we reach for it.

        The idempotency key is the watch itself, so a job retried by the broker
        after a partial failure replays the same reservation instead of making
        a second one.
        """

        for slot in slots:
            try:
                return await self._adapter.book_slot(
                    slot.slot_id,
                    idempotency_key=self._idempotency_key(watch),
                )
            except (SlotUnavailableError, SlotNotFoundError):
                continue
            except AdapterError:
                return None
        return None

    async def _reschedule(self, watch: Watch, now: datetime) -> WatchPollResult:
        """Queue the next check at a jittered offset from now."""

        delay = self._schedule.next_delay()
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

    async def _expire(self, watch: Watch, now: datetime) -> WatchPollResult:
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
