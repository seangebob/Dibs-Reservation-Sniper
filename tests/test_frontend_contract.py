"""Contract-drift guard between the frontend and the real backend schemas.

The frontend hand-maintains `frontend/types/api.ts`. These tests read the live
Pydantic `model_fields` off the actual public models and assert they match,
field-for-field, what the frontend declares and reads -- so any backend
add/remove/rename of a public field trips a test here rather than surfacing as a
silent runtime mismatch in the browser. This is the Milestone 4 Task 12
"contract-drift test against the real backend schemas"; it lives in the backend
suite because that is where the schemas actually are (runnable in CI without a
browser), and it is the authoritative source the TypeScript mirror tracks.

Keep each EXPECTED set identical to the corresponding interface in
`frontend/types/api.ts`.
"""

from backend.models.reservation import (
    AvailabilityQuery,
    AvailabilitySlot,
    BookingConfirmation,
    PromptExecutionResult,
)
from backend.models.watch import Watch
from backend.orchestrator.schemas import ReservationIntent


def test_availability_slot_matches_frontend_type() -> None:
    assert set(AvailabilitySlot.model_fields) == {
        "slot_id",
        "provider",
        "venue_name",
        "venue_type",
        "date",
        "start_time",
        "end_time",
        "party_size",
        "max_party_size",
        "available",
    }


def test_booking_confirmation_matches_frontend_type() -> None:
    assert set(BookingConfirmation.model_fields) == {
        "booking_id",
        "provider",
        "status",
        "slot",
        "created_at",
    }


def test_availability_query_matches_frontend_type() -> None:
    assert set(AvailabilityQuery.model_fields) == {
        "venue_name",
        "venue_type",
        "market",
        "party_size",
        "date",
        "preferred_time",
        "time_window",
        "duration_minutes",
        "special_requests",
    }


def test_prompt_execution_result_matches_frontend_type() -> None:
    assert set(PromptExecutionResult.model_fields) == {
        "status",
        "intent",
        "slots",
        "booking",
        "watch_id",
        "message",
    }


def test_public_watch_matches_frontend_type_and_hides_owner() -> None:
    assert set(Watch.model_fields) == {
        "watch_id",
        "status",
        "query",
        "auto_book",
        "created_at",
        "updated_at",
        "expires_at",
        "attempts",
        "max_attempts",
        "last_checked_at",
        "next_check_at",
        "found_slots",
        "booking",
        "last_error",
    }
    # owner_client_id must never appear in the public Watch model (Req 2.4 / 6.2).
    assert "owner_client_id" not in Watch.model_fields


def test_intent_fields_read_by_the_frontend_exist() -> None:
    # frontend `readIntentView()` reaches into the opaque intent for these; they
    # must exist on the real validated intent.
    assert {
        "venue_name",
        "party_size",
        "date",
        "preferred_time",
        "time_window",
    } <= set(ReservationIntent.model_fields)
