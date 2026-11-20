import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  getAlertEmail: vi.fn(),
  requestAlertEmail: vi.fn(),
  removeAlertEmail: vi.fn(),
}));

import { getAlertEmail, removeAlertEmail, requestAlertEmail } from "@/lib/api";
import { GuestAlerts } from "./GuestAlerts";

async function renderSettled() {
  const result = render(<GuestAlerts />);
  await act(async () => {});
  return result;
}

describe("GuestAlerts", () => {
  beforeEach(() => {
    vi.mocked(getAlertEmail).mockReset();
    vi.mocked(requestAlertEmail).mockReset();
    vi.mocked(removeAlertEmail).mockReset();
  });

  it("asks a guest for an address and says to check their inbox", async () => {
    vi.mocked(getAlertEmail).mockResolvedValue({ ok: true, data: { email: null } });
    vi.mocked(requestAlertEmail).mockResolvedValue({ ok: true, data: undefined });
    await renderSettled();

    fireEvent.change(screen.getByLabelText(/EMAIL ME WHEN A TABLE OPENS/), {
      target: { value: " diner@example.com " },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /SEND LINK/ }));
    });

    expect(requestAlertEmail).toHaveBeenCalledWith("diner@example.com");
    expect(screen.getByRole("status").textContent).toMatch(
      /Check your inbox.*diner@example\.com.*expires in 24 hours/,
    );
  });

  it("shows the server's reason when a link can't be sent", async () => {
    vi.mocked(getAlertEmail).mockResolvedValue({ ok: true, data: { email: null } });
    vi.mocked(requestAlertEmail).mockResolvedValue({
      ok: false,
      status: 429,
      message: "Too many confirmation emails. Try again in an hour.",
    });
    await renderSettled();

    fireEvent.change(screen.getByLabelText(/EMAIL ME/), {
      target: { value: "diner@example.com" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /SEND LINK/ }));
    });

    expect(screen.getByRole("alert").textContent).toMatch(/Too many/);
    expect(screen.getByLabelText(/EMAIL ME/)).toBeTruthy();
  });

  it("names a confirmed address and can stop the emails", async () => {
    vi.mocked(getAlertEmail).mockResolvedValue({
      ok: true,
      data: { email: "diner@example.com" },
    });
    vi.mocked(removeAlertEmail).mockResolvedValue({ ok: true, data: undefined });
    await renderSettled();

    expect(screen.getByRole("status").textContent).toMatch(
      /Alerts go to diner@example\.com/,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /STOP EMAILS/ }));
    });

    expect(removeAlertEmail).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(/EMAIL ME/)).toBeTruthy();
  });

  it("stays hidden when the server can't store an address", async () => {
    vi.mocked(getAlertEmail).mockResolvedValue({
      ok: false,
      status: 503,
      message: "Email alerts are unavailable because PostgreSQL is not configured.",
    });
    const { container } = await renderSettled();

    expect(container.textContent).toBe("");
  });
});
