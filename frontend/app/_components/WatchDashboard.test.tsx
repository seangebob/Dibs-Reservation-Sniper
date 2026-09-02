import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  listMyWatches: vi.fn(),
  cancelWatch: vi.fn(),
}));

import { listMyWatches, cancelWatch } from "@/lib/api";
import { WatchDashboard } from "./WatchDashboard";
import { makeWatch } from "./testFixtures";

describe("WatchDashboard", () => {
  beforeEach(() => {
    vi.mocked(listMyWatches).mockReset();
    vi.mocked(cancelWatch).mockReset();
  });

  it("empty: shows an empty state linking back to the prompt page (Req 4.5)", async () => {
    vi.mocked(listMyWatches).mockResolvedValue({ ok: true, data: [] });
    render(<WatchDashboard />);

    await screen.findByText(/No watches on the board/i);
    const cta = screen.getByText(/OPEN A WATCH/i).closest("a");
    expect(cta?.getAttribute("href")).toBe("/");
  });

  it("lists owned watches, most recently created first (Req 4.1)", async () => {
    const older = makeWatch({
      watch_id: "w_old",
      created_at: "2026-09-01T00:00:00Z",
      query: { ...makeWatch().query, venue_name: "Older Bistro" },
    });
    const newer = makeWatch({
      watch_id: "w_new",
      created_at: "2026-09-02T00:00:00Z",
      query: { ...makeWatch().query, venue_name: "Newer Bistro" },
    });
    // Returned oldest-first; the dashboard must re-order to newest-first.
    vi.mocked(listMyWatches).mockResolvedValue({
      ok: true,
      data: [older, newer],
    });
    render(<WatchDashboard />);

    await screen.findByText("Newer Bistro");
    const order = screen.getAllByText(/Bistro/).map((el) => el.textContent);
    expect(order).toEqual(["Newer Bistro", "Older Bistro"]);
  });

  it("shows an error state with a retry when the load fails", async () => {
    vi.mocked(listMyWatches).mockResolvedValue({
      ok: false,
      message: "Couldn't reach the server.",
    });
    render(<WatchDashboard />);

    await screen.findByText(/SIGNAL LOST/i);
    expect(screen.getByText(/Couldn't reach the server/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /RETRY/i })).toBeTruthy();
  });

  it("cancels a watch in place, updating status without a reload (Req 4.3)", async () => {
    const active = makeWatch({ watch_id: "w1", status: "ACTIVE" });
    vi.mocked(listMyWatches).mockResolvedValue({ ok: true, data: [active] });
    vi.mocked(cancelWatch).mockResolvedValue({
      ok: true,
      data: { ...active, status: "CANCELLED", next_check_at: null },
    });
    render(<WatchDashboard />);

    const button = await screen.findByRole("button", { name: /CANCEL WATCH/i });
    fireEvent.click(button);

    await screen.findByText(/CALLED OFF/i);
    expect(cancelWatch).toHaveBeenCalledWith("w1");
    // The cancelled watch is terminal, so its cancel action is gone.
    expect(screen.queryByRole("button", { name: /CANCEL WATCH/i })).toBeNull();
    // No refetch was triggered — a single initial load only.
    expect(vi.mocked(listMyWatches)).toHaveBeenCalledTimes(1);
  });

  it("surfaces a banner when a cancel fails, leaving the watch active", async () => {
    const active = makeWatch({ watch_id: "w1", status: "ACTIVE" });
    vi.mocked(listMyWatches).mockResolvedValue({ ok: true, data: [active] });
    vi.mocked(cancelWatch).mockResolvedValue({
      ok: false,
      message: "That could not be found.",
    });
    render(<WatchDashboard />);

    fireEvent.click(await screen.findByRole("button", { name: /CANCEL WATCH/i }));

    await screen.findByText(/That could not be found/i);
    // Still cancellable — the watch stayed active.
    expect(screen.getByRole("button", { name: /CANCEL WATCH/i })).toBeTruthy();
  });
});
