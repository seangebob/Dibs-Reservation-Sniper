import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  signup: vi.fn(),
  me: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ getSessionToken: vi.fn() }));

import { logout, me } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { AuthProvider } from "./AuthProvider";
import { AccountBadge } from "./AccountBadge";

const USER = { id: "u1", email: "scout@example.com", created_at: "2026-09-03T12:00:00Z" };

function renderBadge() {
  return render(
    <AuthProvider>
      <AccountBadge />
    </AuthProvider>,
  );
}

describe("AccountBadge (masthead)", () => {
  const assign = vi.fn();

  beforeEach(() => {
    vi.mocked(me).mockReset();
    vi.mocked(logout).mockReset();
    vi.mocked(getSessionToken).mockReset();
    Object.defineProperty(window, "location", {
      value: { assign },
      writable: true,
    });
    assign.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("logged out: offers a way in and never calls /me (Req 5.1)", async () => {
    vi.mocked(getSessionToken).mockReturnValue(null);
    renderBadge();

    const link = await screen.findByText(/sign in/i);
    expect(link.closest("a")?.getAttribute("href")).toBe("/account");
    // No stored token means no round trip: the anonymous flow is untouched.
    expect(me).not.toHaveBeenCalled();
  });

  it("logged in: shows the email and a log-out action (Req 5.1)", async () => {
    vi.mocked(getSessionToken).mockReturnValue("tok-abc");
    vi.mocked(me).mockResolvedValue({ ok: true, data: USER });
    renderBadge();

    expect(await screen.findByText("scout@example.com")).not.toBeNull();
    expect(screen.getByRole("button", { name: /log out/i })).not.toBeNull();
    expect(screen.queryByText(/sign in/i)).toBeNull();
  });

  it("a rejected token hydrates to logged out, not an error (Req 5.3)", async () => {
    vi.mocked(getSessionToken).mockReturnValue("tok-stale");
    vi.mocked(me).mockResolvedValue({
      ok: false,
      status: 401,
      message: "Authentication required.",
    });
    renderBadge();

    expect(await screen.findByText(/sign in/i)).not.toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("logging out clears the session and returns to the front door", async () => {
    vi.mocked(getSessionToken).mockReturnValue("tok-abc");
    vi.mocked(me).mockResolvedValue({ ok: true, data: USER });
    vi.mocked(logout).mockResolvedValue({ ok: true, data: undefined });
    renderBadge();

    fireEvent.click(await screen.findByRole("button", { name: /log out/i }));

    await waitFor(() => expect(logout).toHaveBeenCalled());
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });

  it("renders nothing while hydration is still settling", () => {
    vi.mocked(getSessionToken).mockReturnValue("tok-abc");
    vi.mocked(me).mockReturnValue(new Promise(() => {})); // never settles
    const { container } = renderBadge();

    // No flash of "SIGN IN" at someone who is in fact signed in.
    expect(container.textContent).toBe("");
  });
});
