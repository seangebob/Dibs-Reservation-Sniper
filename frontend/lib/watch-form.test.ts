import { describe, expect, it } from "vitest";

import type { VenueSummary } from "@/types/api";
import {
  MARKET,
  emptyForm,
  formFromIntent,
  matchVenue,
  toQuery,
  venueToday,
  type WatchFormValues,
} from "./watch-form";

const TODAY = "2026-11-19";

function form(over: Partial<WatchFormValues> = {}): WatchFormValues {
  return {
    ...emptyForm(),
    venueName: "The Bauer Kitchen",
    date: "2026-11-21",
    ...over,
  };
}

const CATALOG: VenueSummary[] = [
  { name: "The Bauer Kitchen", venue_type: "RESTAURANT", aliases: ["bauer"] },
  { name: "Ethel's Lounge", venue_type: "RESTAURANT", aliases: ["ethels"] },
  {
    name: "Grand River Rocks",
    venue_type: "RECREATION",
    aliases: ["grr", "grand river rocks climbing gym"],
  },
];

describe("venueToday", () => {
  it("uses the venues' time zone, not the visitor's", () => {
    // 03:00 UTC on Nov 20 is still the evening of Nov 19 in Toronto.
    expect(venueToday(new Date("2026-11-20T03:00:00Z"))).toBe("2026-11-19");
    expect(venueToday(new Date("2026-11-20T06:00:00Z"))).toBe("2026-11-20");
  });
});

describe("toQuery", () => {
  it("builds the exact body POST /api/watches expects", () => {
    const check = toQuery(form({ partySize: " 4 ", time: "19:00" }), TODAY);

    expect(check).toEqual({
      ok: true,
      query: {
        venue_name: "The Bauer Kitchen",
        venue_type: "RESTAURANT",
        market: MARKET,
        party_size: 4,
        date: "2026-11-21",
        preferred_time: "19:00",
        time_window: null,
        duration_minutes: null,
        special_requests: [],
      },
    });
  });

  it("sends a window instead of a time in range mode", () => {
    const check = toQuery(
      form({ timeMode: "range", windowStart: "18:00", windowEnd: "21:00" }),
      TODAY,
    );

    expect(check.ok && check.query.time_window).toEqual({ start: "18:00", end: "21:00" });
    expect(check.ok && check.query.preferred_time).toBeNull();
  });

  it("trims seconds a browser's time input may add", () => {
    const check = toQuery(form({ time: "19:30:00" }), TODAY);

    expect(check.ok && check.query.preferred_time).toBe("19:30");
  });

  it("accepts today", () => {
    expect(toQuery(form({ date: TODAY }), TODAY).ok).toBe(true);
  });

  it.each([
    [{ venueName: "   " }, "Enter the venue"],
    [{ venueName: "x".repeat(201) }, "too long"],
    [{ partySize: "0" }, "1 to 100"],
    [{ partySize: "2.5" }, "1 to 100"],
    [{ partySize: "" }, "1 to 100"],
    [{ partySize: "101" }, "1 to 100"],
    [{ date: "" }, "Pick a date"],
    [{ date: "2026-11-18" }, "today or a later date"],
    [{ time: "" }, "Pick a time"],
    [{ timeMode: "range" as const, windowEnd: "" }, "both ends"],
    [{ timeMode: "range" as const, windowStart: "21:00", windowEnd: "18:00" }, "end after it starts"],
    [{ timeMode: "range" as const, windowStart: "19:00", windowEnd: "19:00" }, "end after it starts"],
  ])("rejects %o", (over, message) => {
    const check = toQuery(form(over), TODAY);

    expect(check.ok).toBe(false);
    expect(!check.ok && check.error).toContain(message);
  });
});

describe("matchVenue", () => {
  it.each([
    ["The Bauer Kitchen", "The Bauer Kitchen"],
    ["the bauer kitchen", "The Bauer Kitchen"],
    ["bauer", "The Bauer Kitchen"],
    ["Ethels Lounge", "Ethel's Lounge"],
    ["ethel’s lounge", "Ethel's Lounge"],
    ["GRR", "Grand River Rocks"],
  ])("matches %s to %s", (typed, expected) => {
    expect(matchVenue(typed, CATALOG)?.name).toBe(expected);
  });

  it.each(["", "Bauer Kitch", "Somewhere new"])("finds nothing for %j", (typed) => {
    expect(matchVenue(typed, CATALOG)).toBeNull();
  });
});

describe("formFromIntent", () => {
  it("prefills every detail a parsed prompt carries", () => {
    const values = formFromIntent({
      venue_name: "Grand River Rocks",
      venue_type: "RECREATION",
      party_size: 6,
      date: "2026-11-22",
      preferred_time: null,
      time_window: { start: "10:00", end: "12:00" },
      duration_minutes: 90,
      special_requests: ["beginner area"],
    });

    expect(values).toMatchObject({
      venueName: "Grand River Rocks",
      venueType: "RECREATION",
      partySize: "6",
      date: "2026-11-22",
      timeMode: "range",
      windowStart: "10:00",
      windowEnd: "12:00",
      durationMinutes: 90,
      specialRequests: ["beginner area"],
    });
    // And it round-trips into the same query, with nothing lost.
    const check = toQuery(values, TODAY);
    expect(check.ok && check.query.duration_minutes).toBe(90);
    expect(check.ok && check.query.special_requests).toEqual(["beginner area"]);
  });

  it("uses an exact time when the prompt gave one", () => {
    const values = formFromIntent({ preferred_time: "19:30", time_window: null });

    expect(values.timeMode).toBe("exact");
    expect(values.time).toBe("19:30");
  });

  it("falls back to defaults for anything missing or malformed", () => {
    const values = formFromIntent({ venue_type: "SPACESHIP", party_size: "4" });

    expect(values).toMatchObject({
      venueName: "",
      venueType: "RESTAURANT",
      partySize: emptyForm().partySize,
      date: "",
      timeMode: "exact",
    });
  });
});
