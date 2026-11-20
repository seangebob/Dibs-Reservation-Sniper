import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ confirmEmail: vi.fn() }));

import { confirmEmail } from "@/lib/api";
import { ConfirmEmail } from "./ConfirmEmail";

async function renderAt(url: string) {
  window.history.replaceState(null, "", url);
  render(<ConfirmEmail />);
  await act(async () => {});
}

describe("ConfirmEmail", () => {
  beforeEach(() => {
    vi.mocked(confirmEmail).mockReset();
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("uses the link's code once and clears it from the URL", async () => {
    vi.mocked(confirmEmail).mockResolvedValue({
      ok: true,
      data: { id: "u1", email: "a@x.com", created_at: "2026-11-20T12:00:00Z", email_verified: true },
    });

    await renderAt("/confirm-email?token=abc123");

    expect(confirmEmail).toHaveBeenCalledTimes(1);
    expect(confirmEmail).toHaveBeenCalledWith("abc123");
    expect(screen.getByRole("status").textContent).toMatch(/EMAIL CONFIRMED.*a@x\.com will get your alerts/);
    expect(window.location.search).toBe("");
  });

  it("explains a used or expired link and points to the account page", async () => {
    vi.mocked(confirmEmail).mockResolvedValue({
      ok: false,
      status: 400,
      message: "This link is invalid, already used, or expired. Ask for a new one.",
    });

    await renderAt("/confirm-email?token=old");

    expect(screen.getByRole("alert").textContent).toMatch(/already used, or expired/);
    expect(screen.getByRole("link", { name: /GO TO YOUR ACCOUNT/ }).getAttribute("href")).toBe("/account");
  });

  it("says so when the link has no code", async () => {
    await renderAt("/confirm-email");

    expect(confirmEmail).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/missing its code/);
  });
});
