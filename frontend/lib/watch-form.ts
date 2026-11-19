/**
 * The manual watch form's logic, kept pure so it is testable without a DOM:
 * form values in, a backend `AvailabilityQuery` (or one readable error) out.
 *
 * The backend validates everything again — these checks exist so a visitor
 * hears about a typo before a round trip, in words rather than a 422.
 */

import type {
  AvailabilityQuery,
  ReservationIntent,
  VenueSummary,
  VenueType,
} from "@/types/api";

/** The only market the backend serves. */
export const MARKET = "Kitchener-Waterloo-Cambridge, ON";

/** Dates are judged in the venues' time zone, not the visitor's. */
const VENUE_TIME_ZONE = "America/Toronto";

export type TimeMode = "exact" | "range";

export interface WatchFormValues {
  venueName: string;
  venueType: VenueType;
  /** Text, as typed — validated into an integer on submit. */
  partySize: string;
  /** YYYY-MM-DD */
  date: string;
  timeMode: TimeMode;
  /** HH:MM, used when timeMode is "exact" */
  time: string;
  /** HH:MM, used when timeMode is "range" */
  windowStart: string;
  windowEnd: string;
  /** Carried through from a parsed prompt; the form doesn't edit these. */
  durationMinutes: number | null;
  specialRequests: string[];
}

export type FormCheck =
  | { ok: true; query: AvailabilityQuery }
  | { ok: false; error: string };

const HH_MM = /^([01]\d|2[0-3]):[0-5]\d$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Today's date (YYYY-MM-DD) where the venues are. */
export function venueToday(now: Date = new Date()): string {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: VENUE_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function emptyForm(): WatchFormValues {
  return {
    venueName: "",
    venueType: "RESTAURANT",
    partySize: "2",
    date: "",
    timeMode: "exact",
    time: "19:00",
    windowStart: "18:00",
    windowEnd: "21:00",
    durationMinutes: null,
    specialRequests: [],
  };
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

/**
 * Prefill the form from a parsed prompt's intent, so "no table — watch
 * instead?" needs no second model call. Missing pieces fall back to defaults.
 */
export function formFromIntent(intent: ReservationIntent): WatchFormValues {
  const raw = (intent ?? {}) as Record<string, unknown>;
  const form = emptyForm();
  const window =
    raw.time_window && typeof raw.time_window === "object"
      ? (raw.time_window as Record<string, unknown>)
      : null;
  const windowStart = window ? str(window.start) : null;
  const windowEnd = window ? str(window.end) : null;
  const preferred = str(raw.preferred_time);
  const type = raw.venue_type;

  return {
    ...form,
    venueName: str(raw.venue_name) ?? "",
    venueType:
      type === "RESTAURANT" || type === "RECREATION" || type === "UNKNOWN"
        ? type
        : form.venueType,
    partySize:
      typeof raw.party_size === "number" ? String(raw.party_size) : form.partySize,
    date: str(raw.date) ?? "",
    timeMode: windowStart && windowEnd ? "range" : "exact",
    time: preferred ?? form.time,
    windowStart: windowStart ?? form.windowStart,
    windowEnd: windowEnd ?? form.windowEnd,
    durationMinutes:
      typeof raw.duration_minutes === "number" ? raw.duration_minutes : null,
    specialRequests: Array.isArray(raw.special_requests)
      ? raw.special_requests.filter((r): r is string => typeof r === "string")
      : [],
  };
}

function normalize(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/['’]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** The catalog venue a typed name or alias refers to, if exactly one. */
export function matchVenue(
  name: string,
  venues: readonly VenueSummary[],
): VenueSummary | null {
  const key = normalize(name);
  if (!key) return null;
  const hits = venues.filter((venue) =>
    [venue.name, ...venue.aliases].some((candidate) => normalize(candidate) === key),
  );
  return hits.length === 1 ? hits[0] : null;
}

/** Validate the form into the exact body `POST /api/watches` expects. */
export function toQuery(values: WatchFormValues, today: string): FormCheck {
  const venueName = values.venueName.trim();
  if (venueName.length === 0) {
    return { ok: false, error: "Enter the venue to watch." };
  }
  if (venueName.length > 200) {
    return { ok: false, error: "That venue name is too long." };
  }

  const party = Number(values.partySize.trim());
  if (!Number.isInteger(party) || party < 1 || party > 100) {
    return { ok: false, error: "Party size must be a whole number from 1 to 100." };
  }

  if (!ISO_DATE.test(values.date)) {
    return { ok: false, error: "Pick a date." };
  }
  if (values.date < today) {
    return { ok: false, error: "Pick today or a later date." };
  }

  let preferredTime: string | null = null;
  let timeWindow: AvailabilityQuery["time_window"] = null;
  if (values.timeMode === "exact") {
    const time = values.time.slice(0, 5);
    if (!HH_MM.test(time)) {
      return { ok: false, error: "Pick a time." };
    }
    preferredTime = time;
  } else {
    const start = values.windowStart.slice(0, 5);
    const end = values.windowEnd.slice(0, 5);
    if (!HH_MM.test(start) || !HH_MM.test(end)) {
      return { ok: false, error: "Pick both ends of the time range." };
    }
    if (start >= end) {
      return { ok: false, error: "The time range must end after it starts." };
    }
    timeWindow = { start, end };
  }

  return {
    ok: true,
    query: {
      venue_name: venueName,
      venue_type: values.venueType,
      market: MARKET,
      party_size: party,
      date: values.date,
      preferred_time: preferredTime,
      time_window: timeWindow,
      duration_minutes: values.durationMinutes,
      special_requests: values.specialRequests,
    },
  };
}
