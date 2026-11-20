import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ verifyAlertEmail: vi.fn() }));

import { verifyAlertEmail } from "@/lib/api";
import { VerifyEmail } from "./VerifyEmail";

async function renderAt(url: string) {
  window.history.replaceState(null, "", url);
  const result = render(<VerifyEmail />);
  await act(async () => {});
  return result;
}

describe("VerifyEmail", () => {
  beforeEach(() => {
    vi.mocked(verifyAlertEmail).mockReset();
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("uses the link's code once, confirms the address, and clears the code from the URL", async () => {
    vi.mocked(verifyAlertEmail).mockResolvedValue({
      ok: true,
      data: { email: "diner@example.com" },
    });

    await renderAt("/verify-email?token=abc123");

    expect(verifyAlertEmail).toHaveBeenCalledTimes(1);
    expect(verifyAlertEmail).toHaveBeenCalledWith("abc123");
    expect(screen.getByRole("status").textContent).toMatch(
      /EMAIL CONFIRMED.*diner@example\.com/,
    );
    expect(window.location.search).toBe("");
  });

  it("explains a used or expired link", async () => {
    vi.mocked(verifyAlertEmail).mockResolvedValue({
      ok: false,
      status: 400,
      message: "This link is invalid, already used, or expired. Ask for a new one.",
    });

    await renderAt("/verify-email?token=old");

    expect(screen.getByRole("alert").textContent).toMatch(/already used, or expired/);
  });

  it("says so when the link has no code", async () => {
    await renderAt("/verify-email");

    expect(verifyAlertEmail).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/missing its code/);
  });
});
