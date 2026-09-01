"""Watch a venue that has no availability and print each background poll.

Runs the real WatchService against the in-process asyncio queue, so it needs
no Redis, no Celery, and no OpenAI key. The poll interval is compressed to a
few seconds so the jitter is visible in one sitting.

    PYTHONPATH=. python3 scripts/watch_demo.py
"""

import asyncio
from datetime import UTC, datetime, timedelta

from backend.db.repositories.watches import InMemoryWatchRepository
from backend.integrations.mock_booking import MockBookingAdapter
from backend.models.reservation import AvailabilityQuery
from backend.models.watch import WatchStatus
from backend.orchestrator.schemas import VenueType
from backend.services.watch_service import WatchService
from backend.workers.queue import AsyncioTaskQueue
from backend.workers.scheduler import PollSchedule


POLLS_BEFORE_AVAILABILITY = 3


class EventuallyAvailableAdapter(MockBookingAdapter):
    """Returns nothing until the table 'opens up' on a later poll."""

    def __init__(self) -> None:
        super().__init__()
        self.searches = 0

    async def search_availability(self, query: AvailabilityQuery):
        self.searches += 1
        if self.searches <= POLLS_BEFORE_AVAILABILITY:
            print(f"  poll {self.searches}: no tables")
            return []
        print(f"  poll {self.searches}: a table opened up")
        return await super().search_availability(query)


async def main() -> None:
    adapter = EventuallyAvailableAdapter()
    repository = InMemoryWatchRepository()
    service: WatchService | None = None

    async def poll(watch_id: str) -> None:
        await service.poll_once(watch_id)

    queue = AsyncioTaskQueue(poll)
    service = WatchService(
        repository,
        adapter,
        queue,
        # Compressed from the 180s +/- 30s production default so the demo is
        # watchable; the ratio of jitter to interval is the same.
        schedule=PollSchedule(interval_seconds=2.0, jitter_seconds=0.5),
        max_attempts=20,
    )

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    query = AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date=tomorrow,
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )

    print(f"Watching Cote for 4 on {tomorrow} at 19:00")
    watch = await service.create(query, auto_book=True)
    print(f"Created {watch.watch_id} (auto-book on)\n")

    previous_check: datetime | None = None
    for _ in range(60):
        await asyncio.sleep(0.25)
        current = await repository.get(watch.watch_id)
        if current.last_checked_at != previous_check:
            previous_check = current.last_checked_at
            if current.next_check_at is not None:
                gap = (current.next_check_at - current.last_checked_at).total_seconds()
                print(f"    -> next check in {gap:.2f}s (jittered)")
        if current.status.is_terminal:
            break

    final = await repository.get(watch.watch_id)
    print(f"\nFinal status: {final.status.value} after {final.attempts} attempt(s)")
    if final.status is WatchStatus.BOOKED:
        slot = final.booking.slot
        print(f"Booked {slot.venue_name} at {slot.start_time} ({final.booking.booking_id})")

    await queue.close()


if __name__ == "__main__":
    asyncio.run(main())
