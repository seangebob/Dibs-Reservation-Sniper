import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WatchCard } from "./WatchCard";
import { makeWatch, makeBooking } from "./testFixtures";

// A fixed clock 5 minutes before the default next_check_at.
const NOW = Date.parse("2026-09-02T12:00:00Z");
const noop = vi.fn();

describe("WatchCard", () => {
  it("ACTIVE: venue, party, attempts, next-check countdown, and a cancel action (Req 4.1, 4.2)", () => {
    const { container } = render(
      <WatchCard
        watch={makeWatch({
          status: "ACTIVE",
          attempts: 3,
          max_attempts: 100,
          next_check_at: "2026-09-02T12:05:00Z",
        })}
        now={NOW}
        cancelling={false}
        onCancel={noop}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Cote");
    expect(text).toMatch(/PARTY 4/);
    expect(text).toMatch(/3 \/ 100 checks/);
    expect(text).toMatch(/next in 5 min/);
    expect(text).toMatch(/TRACKING/);
    expect(screen.getByRole("button", { name: /CANCEL WATCH/i })).toBeTruthy();
  });

  it("FOUND: surfaced distinctly and offers no cancel action (Req 4.4)", () => {
    const { container } = render(
      <WatchCard
        watch={makeWatch({ status: "FOUND", next_check_at: null })}
        now={NOW}
        cancelling={false}
        onCancel={noop}
      />,
    );
    expect(container.textContent).toMatch(/IN SIGHTS/);
    expect(screen.queryByRole("button", { name: /CANCEL/i })).toBeNull();
  });

  it("BOOKED: shows the booking id and no cancel action (Req 4.4)", () => {
    const { container } = render(
      <WatchCard
        watch={makeWatch({
          status: "BOOKED",
          next_check_at: null,
          booking: makeBooking({ booking_id: "bk_9" }),
        })}
        now={NOW}
        cancelling={false}
        onCancel={noop}
      />,
    );
    expect(container.textContent).toMatch(/CONFIRMED/);
    expect(container.textContent).toContain("#bk_9");
    expect(screen.queryByRole("button", { name: /CANCEL/i })).toBeNull();
  });

  it("CANCELLED: muted status, no cancel action, no live countdown", () => {
    const { container } = render(
      <WatchCard
        watch={makeWatch({ status: "CANCELLED", next_check_at: null })}
        now={NOW}
        cancelling={false}
        onCancel={noop}
      />,
    );
    expect(container.textContent).toMatch(/CALLED OFF/);
    expect(container.textContent).not.toMatch(/next in/);
    expect(screen.queryByRole("button", { name: /CANCEL/i })).toBeNull();
  });

  it("shows the busy label while a cancel is in flight", () => {
    render(
      <WatchCard
        watch={makeWatch({ status: "ACTIVE" })}
        now={NOW}
        cancelling={true}
        onCancel={noop}
      />,
    );
    const button = screen.getByRole("button", {
      name: /CANCELLING/i,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
