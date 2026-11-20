import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ resetPassword: vi.fn() }));

import { resetPassword } from "@/lib/api";
import { ResetPassword } from "./ResetPassword";

const assign = vi.fn();
const realLocation = window.location;

/** Render as if the reset email's link was opened (jsdom can't navigate). */
async function renderAt(search: string) {
  Object.defineProperty(window, "location", {
    value: { assign, search, pathname: "/reset-password" },
    writable: true,
  });
  render(<ResetPassword />);
  await act(async () => {});
}

describe("ResetPassword", () => {
  beforeEach(() => {
    vi.mocked(resetPassword).mockReset();
    assign.mockReset();
  });
  afterEach(() => {
    Object.defineProperty(window, "location", { value: realLocation, writable: true });
  });

  it("sets the new password with the link's code, then goes to the watches", async () => {
    vi.mocked(resetPassword).mockResolvedValue({
      ok: true,
      data: { token: "tok", user: { id: "u1", email: "a@x.com", created_at: "2026-11-20T12:00:00Z" } },
    });
    await renderAt("?token=abc123");

    fireEvent.change(screen.getByLabelText(/NEW PASSWORD/), { target: { value: "new-password" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /SET NEW PASSWORD/ }));
    });

    expect(resetPassword).toHaveBeenCalledWith("abc123", "new-password");
    expect(assign).toHaveBeenCalledWith("/watches");
  });

  it("shows why a link failed and offers a new one", async () => {
    vi.mocked(resetPassword).mockResolvedValue({
      ok: false,
      status: 400,
      message: "This link is invalid, already used, or expired. Ask for a new one.",
    });
    await renderAt("?token=old");

    fireEvent.change(screen.getByLabelText(/NEW PASSWORD/), { target: { value: "new-password" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /SET NEW PASSWORD/ }));
    });

    expect(screen.getByRole("alert").textContent).toMatch(/already used, or expired/);
    expect(screen.getByRole("link", { name: /Ask for a new link/ }).getAttribute("href")).toBe("/account");
  });

  it("says so when the link has no code", async () => {
    await renderAt("");

    expect(screen.getByRole("alert").textContent).toMatch(/missing its code/);
    expect(resetPassword).not.toHaveBeenCalled();
  });
});
