import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent } from "@testing-library/react";

// Mock the API boundary so the console's state machine is tested in isolation.
vi.mock("@/lib/api", () => ({
  parseAndBook: vi.fn(),
  createWatch: vi.fn(),
  listVenues: vi.fn(),
}));

import { createWatch, listVenues, parseAndBook, type ApiResult } from "@/lib/api";
import { venueToday } from "@/lib/watch-form";
import type { PromptExecutionResult } from "@/types/api";
import { PromptConsole } from "./PromptConsole";
import { makeResult, makeWatch } from "./testFixtures";

describe("PromptConsole", () => {
  beforeEach(() => {
    vi.mocked(parseAndBook).mockReset();
  });

  it("renders a single free-form prompt input and a submit action (Req 1.1)", () => {
    render(<PromptConsole />);
    expect(screen.getByPlaceholderText(/this Saturday/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /ACQUIRE/i })).toBeTruthy();
    // No structured party-size / date / venue fields.
    expect(screen.queryByLabelText(/party size/i)).toBeNull();
  });

  it("keeps submit disabled until the prompt is non-empty", () => {
    render(<PromptConsole />);
    const button = screen.getByRole("button", {
      name: /ACQUIRE/i,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "watch Cote for 4" },
    });
    expect(button.disabled).toBe(false);
  });

  it("disables the input in flight, calls the API, then shows and preserves state (Req 1.2, 1.3)", async () => {
    let resolve!: (v: ApiResult<PromptExecutionResult>) => void;
    const deferred = new Promise<ApiResult<PromptExecutionResult>>((r) => {
      resolve = r;
    });
    vi.mocked(parseAndBook).mockReturnValue(deferred);

    render(<PromptConsole />);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "watch Cote for 4 Saturday" } });
    fireEvent.click(screen.getByRole("button", { name: /ACQUIRE/i }));

    // In flight: input locked, button shows the busy label (Req 1.2).
    expect(input.disabled).toBe(true);
    expect(screen.getByRole("button", { name: /ACQUIRING/i })).toBeTruthy();
    expect(parseAndBook).toHaveBeenCalledWith("watch Cote for 4 Saturday");

    resolve({
      ok: true,
      data: makeResult({
        status: "WATCH_CREATED",
        watch_id: "watch_1",
        message: "Watching Cote.",
      }),
    });
    await screen.findByText(/WATCH CREATED/i);

    // The prompt text survives so the visitor can refine it (Req 1.3).
    expect(input.value).toBe("watch Cote for 4 Saturday");
    expect(input.disabled).toBe(false);
  });

  it("surfaces a normalized error without a page reload (Req 1.7)", async () => {
    vi.mocked(parseAndBook).mockResolvedValue({
      ok: false,
      message: "Couldn't reach the server. Check your connection and try again.",
      status: undefined,
    });
    render(<PromptConsole />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "watch Cote" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ACQUIRE/i }));
    await screen.findByText(/SIGNAL LOST/i);
    expect(screen.getByText(/Couldn't reach the server/i)).toBeTruthy();
  });

  it("turns a no-table result into a watch without a second model call", async () => {
    const [y, m, d] = venueToday().split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d + 3)).toISOString().slice(0, 10);
    vi.mocked(parseAndBook).mockResolvedValue({
      ok: true,
      data: makeResult({
        status: "NO_AVAILABILITY",
        message: "No availability matched the request.",
        intent: {
          venue_name: "The Bauer Kitchen",
          venue_type: "RESTAURANT",
          party_size: 4,
          date,
          preferred_time: null,
          time_window: { start: "18:00", end: "21:00" },
          duration_minutes: null,
          special_requests: ["window seat"],
        },
      }),
    });
    vi.mocked(listVenues).mockResolvedValue({ ok: true, data: [] });
    vi.mocked(createWatch).mockResolvedValue({
      ok: true,
      data: makeWatch({ auto_book: true }),
    });

    render(<PromptConsole />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "book bauer for 4 this weekend 6 to 9" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ACQUIRE/i }));
    fireEvent.click(await screen.findByRole("button", { name: /WATCH FOR AN OPENING/ }));
    await act(async () => {});

    // Prefilled from the parsed intent; the visitor picks auto-book.
    expect((screen.getByLabelText("VENUE") as HTMLInputElement).value).toBe(
      "The Bauer Kitchen",
    );
    fireEvent.click(screen.getByRole("radio", { name: /Auto-book/ }));
    fireEvent.click(screen.getByRole("button", { name: /WATCH & AUTO-BOOK/ }));

    expect(await screen.findByText(/WATCH CREATED · AUTO-BOOK ON/)).toBeTruthy();
    expect(createWatch).toHaveBeenCalledWith(
      {
        venue_name: "The Bauer Kitchen",
        venue_type: "RESTAURANT",
        market: "Kitchener-Waterloo-Cambridge, ON",
        party_size: 4,
        date,
        preferred_time: null,
        time_window: { start: "18:00", end: "21:00" },
        duration_minutes: null,
        special_requests: ["window seat"],
      },
      true,
    );
    // The prompt was parsed once; the watch reused that result.
    expect(parseAndBook).toHaveBeenCalledTimes(1);
  });
});
