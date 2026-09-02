import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Mock the API boundary so the console's state machine is tested in isolation.
vi.mock("@/lib/api", () => ({ parseAndBook: vi.fn() }));

import { parseAndBook, type ApiResult } from "@/lib/api";
import type { PromptExecutionResult } from "@/types/api";
import { PromptConsole } from "./PromptConsole";
import { makeResult } from "./testFixtures";

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
});
