import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  parseAndBook: vi.fn(),
  createWatch: vi.fn(),
  listVenues: vi.fn(),
}));

import { listVenues, parseAndBook } from "@/lib/api";
import { EntryPanel } from "./EntryPanel";

/** Render, then let the venue-list load settle inside act(). */
async function renderSettled(ui: React.ReactElement) {
  const result = render(ui);
  await act(async () => {});
  return result;
}


describe("EntryPanel", () => {
  beforeEach(() => {
    vi.mocked(listVenues).mockResolvedValue({ ok: true, data: [] });
  });

  it("starts on the prompt, with the form one tab away", async () => {
    await renderSettled(<EntryPanel />);

    expect(
      screen.getByRole("tab", { name: "DESCRIBE IT" }).getAttribute("aria-selected"),
    ).toBe("true");
    const prompt = screen.getByPlaceholderText(/this Saturday/i);
    const venue = screen.getByLabelText("VENUE");
    const panelOf = (el: HTMLElement) => el.closest('[role="tabpanel"]') as HTMLElement;
    expect(panelOf(prompt).hidden).toBe(false);
    expect(panelOf(venue).hidden).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: "FILL IN A FORM" }));

    expect(panelOf(venue).hidden).toBe(false);
    expect(panelOf(prompt).hidden).toBe(true);
    expect(
      screen.getByRole("tab", { name: "FILL IN A FORM" }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("keeps what was typed when switching tabs", async () => {
    await renderSettled(<EntryPanel />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "watch bauer for 4" },
    });

    fireEvent.click(screen.getByRole("tab", { name: "FILL IN A FORM" }));
    fireEvent.click(screen.getByRole("tab", { name: "DESCRIBE IT" }));

    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(
      "watch bauer for 4",
    );
  });

  it("sends the visitor to the form when the model is down, keeping the prompt", async () => {
    vi.mocked(parseAndBook).mockResolvedValue({
      ok: false,
      message: "The reservation assistant is temporarily unavailable.",
      status: 503,
    });
    await renderSettled(<EntryPanel />);
    const prompt = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(prompt, { target: { value: "watch bauer for 4" } });
    fireEvent.click(screen.getByRole("button", { name: /ACQUIRE/i }));

    fireEvent.click(
      await screen.findByRole("button", { name: /FILL IN A FORM INSTEAD/i }),
    );

    const formTab = screen.getByRole("tab", { name: "FILL IN A FORM" });
    expect(formTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(formTab);
    const venue = screen.getByLabelText("VENUE");
    expect((venue.closest('[role="tabpanel"]') as HTMLElement).hidden).toBe(false);
    expect(prompt.value).toBe("watch bauer for 4");
  });
});
