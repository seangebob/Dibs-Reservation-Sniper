import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ResultView } from "./ResultView";
import { makeResult, makeSlot, makeBooking } from "./testFixtures";

const noop = () => {};

describe("ResultView", () => {
  it("renders nothing while idle", () => {
    const { container } = render(
      <ResultView state={{ phase: "idle" }} onRetry={noop} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows a loading state", () => {
    const { container } = render(
      <ResultView state={{ phase: "loading" }} onRetry={noop} />,
    );
    expect(container.textContent).toMatch(/ACQUIRING TARGET/i);
  });

  it("WATCH_CREATED: names the watch and links to the dashboard (Req 1.6)", () => {
    const { container } = render(
      <ResultView
        state={{
          phase: "result",
          result: makeResult({
            status: "WATCH_CREATED",
            watch_id: "watch_1",
            message: "Watching Cote for 4.",
          }),
        }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toContain("Cote");
    expect(container.textContent).toMatch(/WATCH CREATED/i);
    expect(container.querySelector('a[href="/watches"]')).not.toBeNull();
  });

  it("AVAILABILITY_FOUND: lists slots and never implies a booking (Req 1.4)", () => {
    const { container } = render(
      <ResultView
        state={{
          phase: "result",
          result: makeResult({
            status: "AVAILABILITY_FOUND",
            slots: [makeSlot()],
            message: "Found 1 mock slot.",
          }),
        }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toMatch(/AVAILABILITY FOUND/i);
    expect(container.textContent).toMatch(/PARTY 4/);
    expect(container.textContent).not.toMatch(/CONFIRMED/i);
  });

  it("MOCK_BOOKED: shows the confirmation and labels it a demo (Req 1.5)", () => {
    const { container } = render(
      <ResultView
        state={{
          phase: "result",
          result: makeResult({
            status: "MOCK_BOOKED",
            slots: [makeSlot()],
            booking: makeBooking({ booking_id: "bk_9" }),
            message: "Mock reservation confirmed.",
          }),
        }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toMatch(/MOCK BOOKED/i);
    expect(container.textContent).toContain("#bk_9");
    expect(container.textContent).toMatch(/no real reservation/i);
  });

  it("NO_AVAILABILITY: names the venue without claiming a booking (Req 1.4)", () => {
    const { container } = render(
      <ResultView
        state={{
          phase: "result",
          result: makeResult({
            status: "NO_AVAILABILITY",
            message: "No availability matched the request.",
          }),
        }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toMatch(/NO TABLE IN RANGE/i);
    expect(container.textContent).toContain("Cote");
    // Offers a watch, but opens no form (and loads nothing) until asked.
    expect(screen.getByRole("button", { name: /WATCH FOR AN OPENING/ })).toBeTruthy();
    expect(screen.queryByLabelText("VENUE")).toBeNull();
  });

  it("CLARIFICATION_REQUIRED: shows the returned question (Req 1.3)", () => {
    const { container } = render(
      <ResultView
        state={{
          phase: "result",
          result: makeResult({
            status: "CLARIFICATION_REQUIRED",
            message: "How many guests are there?",
          }),
        }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toContain("How many guests are there?");
    expect(container.textContent).toMatch(/NEEDS ONE DETAIL/i);
  });

  it("error (503 service): plain message, no raw error tokens, retry works (Req 1.7)", () => {
    const onRetry = vi.fn();
    const { container } = render(
      <ResultView
        state={{
          phase: "error",
          message: "The reservation assistant is temporarily unavailable.",
          status: 503,
        }}
        onRetry={onRetry}
      />,
    );
    expect(container.textContent).toMatch(/SIGNAL LOST/i);
    expect(container.textContent).toMatch(/temporarily unavailable/i);
    // Never leak internals to the visitor (Req 1.7).
    expect(container.textContent).not.toMatch(/exception|stack|\{"detail"|500/i);

    fireEvent.click(screen.getByRole("button", { name: /RETRY/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it.each([502, 503])(
    "error (%i, the model can't read prompts): offers the form, which needs no model",
    (status) => {
      const onUseForm = vi.fn();
      render(
        <ResultView
          state={{ phase: "error", message: "Temporarily unavailable.", status }}
          onRetry={noop}
          onUseForm={onUseForm}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: /FILL IN A FORM INSTEAD/i }));
      expect(onUseForm).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: /RETRY/i })).toBeTruthy();
    },
  );

  it("error (API unreachable): no form offer, since the form needs the API too", () => {
    render(
      <ResultView
        state={{ phase: "error", message: "Couldn't reach the server." }}
        onRetry={noop}
        onUseForm={noop}
      />,
    );

    expect(screen.queryByRole("button", { name: /FORM/i })).toBeNull();
    expect(screen.getByRole("button", { name: /RETRY/i })).toBeTruthy();
  });

  it("error (422 client): uses the distinct non-service framing", () => {
    const { container } = render(
      <ResultView
        state={{ phase: "error", message: "Something went wrong.", status: 422 }}
        onRetry={noop}
      />,
    );
    expect(container.textContent).toMatch(/NO SHOT/i);
  });
});
