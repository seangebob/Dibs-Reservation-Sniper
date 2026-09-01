/**
 * Hand-maintained mirror of the backend's public JSON contracts.
 *
 * Kept in sync by eye with `backend/orchestrator/schemas.py`,
 * `backend/models/reservation.py`, and `backend/models/watch.py`. Only the
 * fields the frontend actually reads are typed; the backend may send more.
 */

export type VenueType = "RESTAURANT" | "RECREATION";

export type WatchStatus =
  | "ACTIVE"
  | "FOUND"
  | "BOOKED"
  | "EXPIRED"
  | "CANCELLED";

export type ExecutionStatus =
  | "CLARIFICATION_REQUIRED"
  | "AVAILABILITY_FOUND"
  | "NO_AVAILABILITY"
  | "MOCK_BOOKED"
  | "WATCH_REQUIRED"
  | "WATCH_CREATED";

export interface TimeWindow {
  start: string;
  end: string;
}

export interface AvailabilityQuery {
  venue_name: string;
  venue_type: VenueType;
  market: string;
  party_size: number;
  date: string;
  preferred_time: string | null;
  time_window: TimeWindow | null;
  duration_minutes: number | null;
  special_requests: string[];
}

export interface AvailabilitySlot {
  slot_id: string;
  provider: "mock";
  venue_name: string;
  venue_type: VenueType;
  date: string;
  start_time: string;
  end_time: string | null;
  party_size: number;
  max_party_size: number;
  available: true;
}

export interface BookingConfirmation {
  booking_id: string;
  provider: "mock";
  status: string;
  slot: AvailabilitySlot;
  created_at: string;
}

export interface Watch {
  watch_id: string;
  status: WatchStatus;
  query: AvailabilityQuery;
  auto_book: boolean;
  created_at: string;
  updated_at: string;
  expires_at: string;
  attempts: number;
  max_attempts: number;
  last_checked_at: string | null;
  next_check_at: string | null;
  found_slots: AvailabilitySlot[];
  booking: BookingConfirmation | null;
  last_error: string | null;
}

/** The orchestrator's validated intent; the frontend treats it as opaque. */
export type ReservationIntent = Record<string, unknown>;

export interface PromptExecutionResult {
  status: ExecutionStatus;
  intent: ReservationIntent;
  slots: AvailabilitySlot[];
  booking: BookingConfirmation | null;
  watch_id: string | null;
  message: string;
}
