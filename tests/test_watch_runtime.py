"""The internal WatchRuntime sidecar and its v1 migration.

WatchRuntime carries the concurrency and policy metadata that must never
appear in the public Watch JSON: revision, fencing state, cadence window,
outage counter, and terminal cleanup time. Legacy watches that predate the
sidecar are migrated on read without reopening terminal state or silently
raising a legacy attempt ceiling.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.models.reservation import AvailabilityQuery
from backend.models.watch import Watch, WatchStatus
from backend.models.watch_runtime import (
    RuntimePhase,
    WatchRuntime,
    initial_runtime,
    migrate_legacy_watch,
    window_id_for,
)
from backend.orchestrator.schemas import VenueType


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EARLIEST_DELAY = 150.0


def _query() -> AvailabilityQuery:
    return AvailabilityQuery(
        venue_name="Cote",
        venue_type=VenueType.RESTAURANT,
        market="Kitchener-Waterloo-Cambridge, ON",
        party_size=4,
        date="2026-09-05",
        preferred_time="19:00",
        time_window=None,
        duration_minutes=None,
        special_requests=[],
    )


def _watch(
    *,
    status: WatchStatus = WatchStatus.ACTIVE,
    attempts: int = 0,
    max_attempts: int = 25_000,
    expires_at: datetime | None = None,
    next_check_at: datetime | None = NOW,
    **extra: object,
) -> Watch:
    if status.is_terminal:
        next_check_at = None
    return Watch(
        watch_id="watch_abc",
        status=status,
        query=_query(),
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at or datetime(2026, 9, 6, tzinfo=UTC),
        attempts=attempts,
        max_attempts=max_attempts,
        next_check_at=next_check_at,
        **extra,  # type: ignore[arg-type]
    )


def test_window_id_is_deterministic_from_watch_and_sequence() -> None:
    assert window_id_for("watch_abc", 0) == "watch_abc:0"
    assert window_id_for("watch_abc", 7) == "watch_abc:7"


def test_a_fresh_runtime_is_schema_version_two_at_revision_zero() -> None:
    runtime = initial_runtime(
        _watch(),
        required_attempts=2593,
        supports_deadline=True,
    )

    assert runtime.schema_version == 2
    assert runtime.revision == 0
    assert runtime.required_attempts == 2593
    assert runtime.supports_deadline is True
    assert runtime.consecutive_outages == 0
    assert runtime.cadence_sequence == 0
    assert runtime.window_id == "watch_abc:0"
    assert runtime.scheduled_for == NOW
    assert runtime.phase is None
    assert runtime.cancel_requested is False
    assert runtime.terminal_delete_at is None


def test_runtime_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        WatchRuntime(
            required_attempts=1,
            supports_deadline=True,
            scheduled_for=datetime(2026, 9, 1, 12, 0),  # naive
        )


def test_runtime_rejects_negative_counters() -> None:
    with pytest.raises(ValueError):
        WatchRuntime(
            required_attempts=1,
            supports_deadline=True,
            consecutive_outages=-1,
        )


# --- v1 migration -----------------------------------------------------------


def test_migrating_an_active_watch_derives_a_current_window() -> None:
    watch = _watch(next_check_at=NOW)

    runtime = migrate_legacy_watch(
        watch, earliest_delay_seconds=EARLIEST_DELAY, now=NOW
    )

    assert runtime.schema_version == 2
    assert runtime.revision == 0
    assert runtime.cadence_sequence == 0
    assert runtime.window_id == "watch_abc:0"
    # An active watch keeps its persisted due time.
    assert runtime.scheduled_for == NOW
    assert runtime.phase is None


def test_migration_preserves_a_legacy_ceiling_and_reports_it_as_limited() -> None:
    """A 200-ceiling multi-day watch migrates as attempt-limited, not raised."""

    watch = _watch(
        max_attempts=200,
        expires_at=datetime(2026, 10, 1, tzinfo=UTC),  # ~30 days out
    )

    runtime = migrate_legacy_watch(
        watch, earliest_delay_seconds=EARLIEST_DELAY, now=NOW
    )

    assert runtime.required_attempts > 200
    assert runtime.supports_deadline is False


def test_migration_marks_a_generous_ceiling_deadline_capable() -> None:
    watch = _watch(
        max_attempts=25_000,
        expires_at=datetime(2026, 9, 6, tzinfo=UTC),  # ~4.5 days
    )

    runtime = migrate_legacy_watch(
        watch, earliest_delay_seconds=EARLIEST_DELAY, now=NOW
    )

    assert runtime.supports_deadline is True


def test_migration_accounts_for_attempts_already_spent() -> None:
    """remaining_required_total includes attempts already committed."""

    expires_at = datetime(2026, 9, 6, tzinfo=UTC)
    fresh = migrate_legacy_watch(
        _watch(attempts=0, expires_at=expires_at),
        earliest_delay_seconds=EARLIEST_DELAY,
        now=NOW,
    )
    spent = migrate_legacy_watch(
        _watch(attempts=100, expires_at=expires_at),
        earliest_delay_seconds=EARLIEST_DELAY,
        now=NOW,
    )

    assert spent.required_attempts == fresh.required_attempts + 100


def test_migrating_a_terminal_watch_carries_no_schedule() -> None:
    watch = _watch(status=WatchStatus.EXPIRED, attempts=200, max_attempts=200)

    runtime = migrate_legacy_watch(
        watch, earliest_delay_seconds=EARLIEST_DELAY, now=NOW
    )

    assert runtime.window_id is None
    assert runtime.scheduled_for is None
    assert runtime.phase is None


def test_migrating_an_active_watch_without_a_due_time_uses_now() -> None:
    watch = _watch(next_check_at=None)

    runtime = migrate_legacy_watch(
        watch, earliest_delay_seconds=EARLIEST_DELAY, now=NOW
    )

    assert runtime.scheduled_for == NOW
    assert runtime.window_id == "watch_abc:0"


def test_runtime_round_trips_through_json() -> None:
    runtime = initial_runtime(
        _watch(), required_attempts=10, supports_deadline=True
    )
    restored = WatchRuntime.model_validate_json(runtime.model_dump_json())

    assert restored == runtime
    assert restored.phase is None


def test_runtime_phase_serializes_as_its_string_value() -> None:
    runtime = initial_runtime(
        _watch(),
        required_attempts=10,
        supports_deadline=True,
        phase=RuntimePhase.BOOKING,
    )

    assert '"BOOKING"' in runtime.model_dump_json()
