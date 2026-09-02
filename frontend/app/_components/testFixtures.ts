/**
 * Shared factories for the component tests. Not a test file itself (no
 * `.test.` suffix), so vitest won't try to run it. Venue names are ASCII to
 * keep text assertions free of any encoding fragility.
 */

import type {
  AvailabilitySlot,
  BookingConfirmation,
  PromptExecutionResult,
  ReservationIntent,
  Watch,
} from "@/types/api";

export const sampleIntent: ReservationIntent = {
  venue_name: "Cote",
  party_size: 4,
  date: "2026-09-05",
  preferred_time: null,
  time_window: { start: "18:00", end: "21:00" },
};

export function makeSlot(over: Partial<AvailabilitySlot> = {}): AvailabilitySlot {
  return {
    slot_id: "slot-1",
    provider: "mock",
    venue_name: "Cote",
    venue_type: "RESTAURANT",
    date: "2026-09-05",
    start_time: "18:00",
    end_time: "20:00",
    party_size: 4,
    max_party_size: 8,
    available: true,
    ...over,
  };
}

export function makeBooking(
  over: Partial<BookingConfirmation> = {},
): BookingConfirmation {
  return {
    booking_id: "bk_1",
    provider: "mock",
    status: "MOCK_CONFIRMED",
    slot: makeSlot(),
    created_at: "2026-09-02T00:00:00Z",
    ...over,
  };
}

export function makeResult(
  over: Partial<PromptExecutionResult> = {},
): PromptExecutionResult {
  return {
    status: "WATCH_CREATED",
    intent: sampleIntent,
    slots: [],
    booking: null,
    watch_id: null,
    message: "ok",
    ...over,
  };
}

export function makeWatch(over: Partial<Watch> = {}): Watch {
  return {
    watch_id: "watch_1",
    status: "ACTIVE",
    query: {
      venue_name: "Cote",
      venue_type: "RESTAURANT",
      market: "Kitchener-Waterloo-Cambridge, ON",
      party_size: 4,
      date: "2026-09-05",
      preferred_time: null,
      time_window: { start: "18:00", end: "21:00" },
      duration_minutes: null,
      special_requests: [],
    },
    auto_book: false,
    created_at: "2026-09-02T12:00:00Z",
    updated_at: "2026-09-02T12:00:00Z",
    expires_at: "2026-09-06T00:00:00Z",
    attempts: 3,
    max_attempts: 100,
    last_checked_at: "2026-09-02T12:00:00Z",
    next_check_at: "2026-09-02T12:05:00Z",
    found_slots: [],
    booking: null,
    last_error: null,
    ...over,
  };
}
