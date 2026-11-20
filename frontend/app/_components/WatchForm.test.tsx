import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  createWatch: vi.fn(),
  listVenues: vi.fn(),
  // The guest email box asks for this browser's address; "unavailable" hides it.
  getAlertEmail: vi.fn(async () => ({ ok: false, status: 503, message: "" })),
  requestAlertEmail: vi.fn(),
  removeAlertEmail: vi.fn(),
}));

import { createWatch, listVenues } from "@/lib/api";
import { venueToday } from "@/lib/watch-form";
import { formFromIntent } from "@/lib/watch-form";
import { WatchForm } from "./WatchForm";
import { makeWatch } from "./testFixtures";

/** Render, then let the venue-list load settle inside act(). */
async function renderSettled(ui: React.ReactElement) {
  const result = render(ui);
  await act(async () => {});
  return result;
}


function daysFromToday(days: number): string {
  const [y, m, d] = venueToday().split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

const DATE = daysFromToday(3);

function fill({ venue = "The Bauer Kitchen", party = "4", date = DATE } = {}) {
  fireEvent.change(screen.getByLabelText("VENUE"), { target: { value: venue } });
  fireEvent.change(screen.getByLabelText("PARTY SIZE"), { target: { value: party } });
  fireEvent.change(screen.getByLabelText("DATE"), { target: { value: date } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /START WATCH|AUTO-BOOK ▸/ }));
}

describe("WatchForm", () => {
  beforeEach(() => {
    vi.mocked(createWatch).mockReset();
    vi.mocked(listVenues).mockReset();
    vi.mocked(listVenues).mockResolvedValue({
      ok: true,
      data: [
        { name: "The Bauer Kitchen", venue_type: "RESTAURANT", aliases: ["bauer"] },
        { name: "Grand River Rocks", venue_type: "RECREATION", aliases: ["grr"] },
      ],
    });
  });

  it("defaults to notify-only and posts the watch without any prompt parsing", async () => {
    vi.mocked(createWatch).mockResolvedValue({ ok: true, data: makeWatch() });
    await renderSettled(<WatchForm />);

    expect(
      (screen.getByRole("radio", { name: /Notify me/ }) as HTMLInputElement).checked,
    ).toBe(true);
    fill();
    submit();

    expect(createWatch).toHaveBeenCalledWith(
      {
        venue_name: "The Bauer Kitchen",
        venue_type: "RESTAURANT",
        market: "Kitchener-Waterloo-Cambridge, ON",
        party_size: 4,
        date: DATE,
        preferred_time: "19:00",
        time_window: null,
        duration_minutes: null,
        special_requests: [],
      },
      false,
    );
    expect(await screen.findByText(/WATCH CREATED · NOTIFY ME/)).toBeTruthy();
  });

  it("shows exactly what will be booked before opting into auto-book", async () => {
    vi.mocked(createWatch).mockResolvedValue({
      ok: true,
      data: makeWatch({ auto_book: true }),
    });
    await renderSettled(<WatchForm />);
    fill({ venue: "bauer", party: "6" });
    await screen.findByText(/Known venue/);

    expect(screen.queryByRole("note")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /Auto-book/ }));

    const criteria = screen.getByRole("note");
    expect(criteria.textContent).toContain("The Bauer Kitchen");
    expect(criteria.textContent).toContain("party of 6");
    expect(criteria.textContent).toContain("19:00");
    expect(criteria.textContent).toMatch(/simulated/i);

    submit();
    const [query, autoBook] = vi.mocked(createWatch).mock.calls[0];
    expect(autoBook).toBe(true);
    // The alias is sent as the name the criteria box showed.
    expect(query.venue_name).toBe("The Bauer Kitchen");
    expect(await screen.findByText(/WATCH CREATED · AUTO-BOOK ON/)).toBeTruthy();
  });

  it("fills the venue type from the catalog, and asks for it otherwise", async () => {
    vi.mocked(createWatch).mockResolvedValue({ ok: true, data: makeWatch() });
    await renderSettled(<WatchForm />);
    await screen.findByText(/Pick a suggestion/);

    // Unknown venue: the visitor says what kind of place it is.
    fireEvent.change(screen.getByLabelText("VENUE"), { target: { value: "Somewhere New" } });
    fireEvent.change(screen.getByLabelText("KIND OF PLACE"), {
      target: { value: "UNKNOWN" },
    });

    // A catalog alias: the type comes from the catalog and the question goes.
    fireEvent.change(screen.getByLabelText("VENUE"), { target: { value: "grr" } });
    expect(await screen.findByText(/Known venue — activity hours apply/)).toBeTruthy();
    expect(screen.queryByLabelText("KIND OF PLACE")).toBeNull();

    fill({ venue: "grr" });
    submit();
    expect(vi.mocked(createWatch).mock.calls[0][0]).toMatchObject({
      venue_name: "Grand River Rocks",
      venue_type: "RECREATION",
    });
    await screen.findByText(/WATCH CREATED/);
  });

  it("sends a time range in range mode", async () => {
    vi.mocked(createWatch).mockResolvedValue({ ok: true, data: makeWatch() });
    await renderSettled(<WatchForm />);
    fill();

    fireEvent.click(screen.getByRole("radio", { name: "Time range" }));
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "18:30" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "20:45" } });
    submit();

    expect(vi.mocked(createWatch).mock.calls[0][0]).toMatchObject({
      preferred_time: null,
      time_window: { start: "18:30", end: "20:45" },
    });
    await screen.findByText(/WATCH CREATED/);
  });

  it("explains a problem without calling the API", async () => {
    await renderSettled(<WatchForm />);
    fill({ date: daysFromToday(-1) });

    submit();

    expect(screen.getByRole("alert").textContent).toContain("today or a later date");
    expect(createWatch).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the watch is refused", async () => {
    vi.mocked(createWatch).mockResolvedValue({
      ok: false,
      status: 503,
      message: "Watch service is not configured.",
    });
    await renderSettled(<WatchForm />);
    fill();

    submit();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Watch service is not configured.",
    );
  });

  it("still works when the venue list can't be loaded", async () => {
    vi.mocked(listVenues).mockResolvedValue({ ok: false, message: "offline" });
    vi.mocked(createWatch).mockResolvedValue({ ok: true, data: makeWatch() });
    await renderSettled(<WatchForm />);
    fill();

    submit();

    expect(createWatch).toHaveBeenCalledTimes(1);
    await screen.findByText(/WATCH CREATED/);
  });

  it("links to the dashboard and can start over after creating a watch", async () => {
    vi.mocked(createWatch).mockResolvedValue({ ok: true, data: makeWatch() });
    await renderSettled(<WatchForm />);
    fill();
    submit();

    const link = await screen.findByRole("link", { name: /TRACK IT/ });
    expect(link.getAttribute("href")).toBe("/watches");

    fireEvent.click(screen.getByRole("button", { name: "SET UP ANOTHER" }));
    expect((screen.getByLabelText("VENUE") as HTMLInputElement).value).toBe("");
  });

  it("can be prefilled, e.g. from a prompt that found no table", async () => {
    await renderSettled(
      <WatchForm
        allowAnother={false}
        initial={formFromIntent({
          venue_name: "The Bauer Kitchen",
          venue_type: "RESTAURANT",
          party_size: 4,
          date: DATE,
          preferred_time: null,
          time_window: { start: "18:00", end: "21:00" },
        })}
      />,
    );

    expect((screen.getByLabelText("VENUE") as HTMLInputElement).value).toBe(
      "The Bauer Kitchen",
    );
    expect((screen.getByLabelText("PARTY SIZE") as HTMLInputElement).value).toBe("4");
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("18:00");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("21:00");
  });
});
