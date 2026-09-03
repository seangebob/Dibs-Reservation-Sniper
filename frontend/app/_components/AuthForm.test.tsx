import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  signup: vi.fn(),
  me: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ getSessionToken: vi.fn(() => null) }));

import { login, signup } from "@/lib/api";
import { AuthProvider } from "./AuthProvider";
import { AuthForm } from "./AuthForm";

const USER = { id: "u1", email: "a@x.com", created_at: "2026-09-03T12:00:00Z" };
const OK = { ok: true as const, data: { token: "tok", user: USER } };

function renderForm() {
  return render(
    <AuthProvider>
      <AuthForm />
    </AuthProvider>,
  );
}

function fill(email = "a@x.com", password = "hunter2-secret") {
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: password },
  });
}

describe("AuthForm", () => {
  const assign = vi.fn();

  beforeEach(() => {
    vi.mocked(login).mockReset();
    vi.mocked(signup).mockReset();
    // jsdom refuses a real navigation; the component only needs the call.
    Object.defineProperty(window, "location", {
      value: { assign },
      writable: true,
    });
    assign.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("signs in and sends the visitor to their watches (Req 5.1/5.4)", async () => {
    vi.mocked(login).mockResolvedValue(OK);
    renderForm();

    fill();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(login).toHaveBeenCalledWith("a@x.com", "hunter2-secret"),
    );
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/watches"));
  });

  it("switches to sign-up and creates the account instead", async () => {
    vi.mocked(signup).mockResolvedValue(OK);
    renderForm();

    fireEvent.click(screen.getByRole("button", { name: /create one/i }));
    fill();
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(signup).toHaveBeenCalled());
    expect(login).not.toHaveBeenCalled();
  });

  it("shows the plain-language failure and never the password (Req 5.5)", async () => {
    vi.mocked(login).mockResolvedValue({
      ok: false,
      status: 401,
      message: "Invalid email or password.",
    });
    renderForm();

    fill("a@x.com", "wrong-password");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Invalid email or password.");
    expect(document.body.textContent).not.toContain("wrong-password");
    expect(assign).not.toHaveBeenCalled();
  });

  it("clears the typed password after a rejected attempt", async () => {
    vi.mocked(login).mockResolvedValue({
      ok: false,
      status: 401,
      message: "Invalid email or password.",
    });
    renderForm();

    fill("a@x.com", "wrong-password");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await screen.findByRole("alert");
    expect((screen.getByLabelText(/password/i) as HTMLInputElement).value).toBe("");
  });

  it("cannot be submitted until both fields have content", () => {
    renderForm();

    const submit = screen.getByRole("button", {
      name: /sign in/i,
    }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fill();
    expect(submit.disabled).toBe(false);
  });

  it("surfaces a throttle message as ordinary copy, not a crash (Req 5.5)", async () => {
    vi.mocked(login).mockResolvedValue({
      ok: false,
      status: 429,
      message: "Too many failed login attempts. Try again shortly.",
    });
    renderForm();

    fill();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/too many failed login attempts/i);
  });
});
