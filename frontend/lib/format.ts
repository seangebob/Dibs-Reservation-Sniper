/**
 * Presentation helpers shared by the result branches.
 *
 * The prompt result carries a full `intent` object, but `types/api.ts` keeps it
 * deliberately opaque. `readIntentView` is the one place that reaches into it —
 * defensively, field by field — so the display can name the venue / party / date
 * on the branches (WATCH_CREATED, NO_AVAILABILITY) that carry no slot or booking.
 */

import type { ReservationIntent, TimeWindow } from "@/types/api";

export interface IntentView {
  venueName: string | null;
  partySize: number | null;
  date: string | null;
  preferredTime: string | null;
  timeWindow: TimeWindow | null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function timeWindow(value: unknown): TimeWindow | null {
  if (value && typeof value === "object") {
    const start = str((value as Record<string, unknown>).start);
    const end = str((value as Record<string, unknown>).end);
    if (start && end) {
      return { start, end };
    }
  }
  return null;
}

export function readIntentView(intent: ReservationIntent): IntentView {
  const raw = (intent ?? {}) as Record<string, unknown>;
  return {
    venueName: str(raw.venue_name),
    partySize: num(raw.party_size),
    date: str(raw.date),
    preferredTime: str(raw.preferred_time),
    timeWindow: timeWindow(raw.time_window),
  };
}

const WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
];

/** "2026-09-05" → "SAT · SEP 5". Timezone-safe (parsed as a plain date). */
export function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (Number.isNaN(date.getTime())) return iso;
  return `${WEEKDAYS[date.getDay()]} · ${MONTHS[date.getMonth()]} ${date.getDate()}`;
}

/** A "HH:MM" or "HH:MM:SS" clock value trimmed to "HH:MM". */
export function formatClock(value: string | null): string | null {
  if (!value) return null;
  const match = /^(\d{2}:\d{2})/.exec(value);
  return match ? match[1] : value;
}

/** The time band for a query: a window "18:00–21:00", a single time, or null. */
export function formatTimeBand(
  preferred: string | null,
  window: TimeWindow | null,
): string | null {
  if (window) {
    const start = formatClock(window.start);
    const end = formatClock(window.end);
    if (start && end) return `${start}–${end}`;
  }
  return formatClock(preferred);
}

/**
 * A short countdown to an ISO timestamp: "due now", "in 4 min", "in 2 h",
 * "in 3 d". Returns null for a missing or unparseable value. `now` is
 * injectable (defaults to the wall clock) so the result is deterministic under
 * test and can be driven by a ticking state value on the dashboard.
 */
export function formatCountdown(
  iso: string | null,
  now: number = Date.now(),
): string | null {
  if (!iso) return null;
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return null;
  const minutes = Math.round((target - now) / 60_000);
  if (minutes <= 0) return "due now";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} d`;
}
