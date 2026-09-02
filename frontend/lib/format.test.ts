import { describe, it, expect } from "vitest";
import {
  readIntentView,
  formatDate,
  formatClock,
  formatTimeBand,
  formatCountdown,
} from "./format";
import type { ReservationIntent } from "@/types/api";

describe("readIntentView", () => {
  it("extracts the display fields from a well-formed intent", () => {
    const intent = {
      venue_name: "Côte",
      party_size: 4,
      date: "2026-09-05",
      preferred_time: null,
      time_window: { start: "18:00", end: "21:00" },
      extra: "ignored",
    } as unknown as ReservationIntent;

    expect(readIntentView(intent)).toEqual({
      venueName: "Côte",
      partySize: 4,
      date: "2026-09-05",
      preferredTime: null,
      timeWindow: { start: "18:00", end: "21:00" },
    });
  });

  it("returns nulls for missing, empty, or mistyped fields", () => {
    const intent = {
      venue_name: "   ",
      party_size: "4",
      time_window: { start: "18:00" },
    } as unknown as ReservationIntent;

    expect(readIntentView(intent)).toEqual({
      venueName: null,
      partySize: null,
      date: null,
      preferredTime: null,
      timeWindow: null,
    });
  });

  it("tolerates a null/empty intent without throwing", () => {
    expect(readIntentView(null as unknown as ReservationIntent).venueName).toBeNull();
    expect(readIntentView({} as ReservationIntent).partySize).toBeNull();
  });
});

describe("formatDate", () => {
  it("formats an ISO date as weekday + month day, timezone-safe", () => {
    // 2026-09-05 is a Saturday regardless of the runner's timezone.
    expect(formatDate("2026-09-05")).toBe("SAT · SEP 5");
  });

  it("passes through an unparsable value and handles null", () => {
    expect(formatDate("not-a-date")).toBe("not-a-date");
    expect(formatDate(null)).toBeNull();
  });
});

describe("formatClock", () => {
  it("trims seconds to HH:MM", () => {
    expect(formatClock("19:30:00")).toBe("19:30");
    expect(formatClock("18:00")).toBe("18:00");
    expect(formatClock(null)).toBeNull();
  });
});

describe("formatTimeBand", () => {
  it("prefers a window range over a single time", () => {
    expect(
      formatTimeBand("20:00", { start: "18:00", end: "21:00" }),
    ).toBe("18:00–21:00");
  });

  it("falls back to the preferred time when there is no window", () => {
    expect(formatTimeBand("19:30:00", null)).toBe("19:30");
  });

  it("is null when neither is present", () => {
    expect(formatTimeBand(null, null)).toBeNull();
  });
});

describe("formatCountdown", () => {
  const now = Date.parse("2026-09-05T18:00:00Z");

  it("counts minutes, then hours, then days", () => {
    expect(formatCountdown("2026-09-05T18:03:00Z", now)).toBe("in 3 min");
    expect(formatCountdown("2026-09-05T20:00:00Z", now)).toBe("in 2 h");
    expect(formatCountdown("2026-09-07T18:00:00Z", now)).toBe("in 2 d");
  });

  it("says 'due now' for an elapsed or current time", () => {
    expect(formatCountdown("2026-09-05T18:00:00Z", now)).toBe("due now");
    expect(formatCountdown("2026-09-05T17:58:00Z", now)).toBe("due now");
  });

  it("returns null for a missing or unparseable value", () => {
    expect(formatCountdown(null, now)).toBeNull();
    expect(formatCountdown("not-a-date", now)).toBeNull();
  });
});
