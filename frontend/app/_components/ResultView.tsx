/**
 * The pure view half of the prompt console: a discriminated `ConsoleState` in,
 * the right Night Scope card out. It holds no state and fires no requests — the
 * only interaction it forwards is `onRetry` for the failure branch — which keeps
 * every response branch trivially unit-testable (Task 12).
 */

import type { ReactNode } from "react";
import type {
  AvailabilitySlot,
  PromptExecutionResult,
} from "@/types/api";
import {
  formatDate,
  formatClock,
  formatTimeBand,
  readIntentView,
} from "@/lib/format";

export type ConsoleState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "result"; result: PromptExecutionResult }
  | { phase: "error"; message: string };

type Tone = "amber" | "green" | "muted" | "red";

export function ResultView({
  state,
  onRetry,
}: {
  state: ConsoleState;
  onRetry: () => void;
}) {
  if (state.phase === "idle") return null;

  if (state.phase === "loading") {
    return (
      <div className="result" role="status" aria-live="polite">
        <div className="result-rail" data-tone="amber" />
        <div className="result-body">
          <span className="telemetry">
            <span className="scanline" aria-hidden="true" />
            ACQUIRING TARGET…
          </span>
        </div>
      </div>
    );
  }

  if (state.phase === "error") {
    return <ErrorCard message={state.message} onRetry={onRetry} />;
  }

  return <ResultCard result={state.result} />;
}

/* ---- shells ----------------------------------------------------------- */

function Card({
  tone,
  live,
  children,
}: {
  tone: Tone;
  live?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="result"
      role="status"
      aria-live={live ? "assertive" : "polite"}
    >
      <div className="result-rail" data-tone={tone} />
      <div className="result-body">{children}</div>
    </div>
  );
}

function Meta({ items }: { items: string[] }) {
  const shown = items.filter(Boolean);
  if (shown.length === 0) return null;
  return (
    <div className="meta">
      {shown.map((item, i) => (
        <span key={item} style={{ display: "contents" }}>
          {i > 0 && <span className="sep" aria-hidden="true">·</span>}
          <span>{item}</span>
        </span>
      ))}
    </div>
  );
}

/* ---- branch router ---------------------------------------------------- */

function ResultCard({ result }: { result: PromptExecutionResult }) {
  switch (result.status) {
    case "WATCH_CREATED":
      return <WatchCreated result={result} />;
    case "AVAILABILITY_FOUND":
      return <AvailabilityFound result={result} />;
    case "MOCK_BOOKED":
      return <MockBooked result={result} />;
    case "NO_AVAILABILITY":
      return <NoAvailability result={result} />;
    case "CLARIFICATION_REQUIRED":
      return <Clarification result={result} />;
    case "WATCH_REQUIRED":
    default:
      return <GenericInfo result={result} />;
  }
}

/* ---- branches --------------------------------------------------------- */

function WatchCreated({ result }: { result: PromptExecutionResult }) {
  const intent = readIntentView(result.intent);
  return (
    <Card tone="amber" live>
      <div className="result-head">
        <div>
          <div className="kicker" data-tone="amber">
            WATCH CREATED · TARGET ACQUIRED
          </div>
          <div className="result-title">{intent.venueName ?? "Watch created"}</div>
          <Meta
            items={[
              intent.partySize != null ? `PARTY ${intent.partySize}` : "",
              formatDate(intent.date) ?? "",
              formatTimeBand(intent.preferredTime, intent.timeWindow) ?? "",
            ]}
          />
        </div>
        <span className="pill" data-tone="amber">
          <span className="dot live" data-tone="amber" aria-hidden="true" />
          LOCKED
        </span>
      </div>
      <div className="divider" />
      <div className="result-foot">
        <div className="result-text" style={{ marginTop: 0 }}>
          {result.message}
        </div>
        <a className="result-link" href="/watches">
          TRACK IT ▸
        </a>
      </div>
    </Card>
  );
}

function AvailabilityFound({ result }: { result: PromptExecutionResult }) {
  const venue = result.slots[0]?.venue_name ?? "Availability found";
  return (
    <Card tone="green" live>
      <div className="result-head">
        <div>
          <div className="kicker" data-tone="green">
            AVAILABILITY FOUND
          </div>
          <div className="result-title">{venue}</div>
        </div>
        <span className="pill" data-tone="green">
          <span className="dot" data-tone="green" aria-hidden="true" />
          IN SIGHTS
        </span>
      </div>
      <div className="result-text">{result.message}</div>
      <div className="slots">
        {result.slots.map((slot) => (
          <SlotRow key={slot.slot_id} slot={slot} />
        ))}
      </div>
    </Card>
  );
}

function SlotRow({ slot }: { slot: AvailabilitySlot }) {
  const band = slot.end_time
    ? `${formatClock(slot.start_time)}–${formatClock(slot.end_time)}`
    : formatClock(slot.start_time);
  return (
    <div className="slot">
      <span className="slot-time">
        {formatDate(slot.date)} · {band}
      </span>
      <span>PARTY {slot.party_size}</span>
    </div>
  );
}

function MockBooked({ result }: { result: PromptExecutionResult }) {
  // The MOCK_BOOKED invariant guarantees a booking; guard anyway for types.
  const booking = result.booking;
  const slot = booking?.slot;
  const band = slot?.end_time
    ? `${formatClock(slot.start_time)}–${formatClock(slot.end_time)}`
    : formatClock(slot?.start_time ?? null);
  return (
    <Card tone="green" live>
      <div className="result-head">
        <div>
          <div className="kicker" data-tone="green">
            MOCK BOOKED · CONFIRMED
          </div>
          <div className="result-title sm">{slot?.venue_name ?? "Reservation confirmed"}</div>
          <Meta
            items={[
              slot ? `PARTY ${slot.party_size}` : "",
              formatDate(slot?.date ?? null) ?? "",
              band ?? "",
            ]}
          />
        </div>
        {booking && (
          <div className="mono-id">
            #{booking.booking_id}
            <br />
            <span style={{ color: "var(--text-ghost)" }}>{booking.status}</span>
          </div>
        )}
      </div>
      <div className="disclaimer">
        <InfoIcon />
        Demo confirmation — no real reservation was made.
      </div>
    </Card>
  );
}

function NoAvailability({ result }: { result: PromptExecutionResult }) {
  const intent = readIntentView(result.intent);
  return (
    <Card tone="muted">
      <div className="kicker" data-tone="muted">
        NO TABLE IN RANGE — YET
      </div>
      <div className="result-title sm">{intent.venueName ?? "No availability"}</div>
      <div className="result-text">{result.message}</div>
    </Card>
  );
}

function Clarification({ result }: { result: PromptExecutionResult }) {
  return (
    <Card tone="amber" live>
      <div className="kicker" data-tone="amber">
        NEEDS ONE DETAIL
      </div>
      <div className="result-title q">{result.message}</div>
      <div className="refine">
        <span style={{ color: "var(--amber)" }}>&gt;</span>
        Add the missing detail to your prompt above and acquire again.
      </div>
    </Card>
  );
}

function GenericInfo({ result }: { result: PromptExecutionResult }) {
  return (
    <Card tone="amber">
      <div className="kicker" data-tone="amber">
        HEADS UP
      </div>
      <div className="result-title sm">{result.message}</div>
    </Card>
  );
}

function ErrorCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card tone="red" live>
      <div className="kicker" data-tone="red">
        SIGNAL LOST
      </div>
      <div className="result-title sm">{message}</div>
      <div className="result-text">
        No watch was created. Give it another shot in a moment.
      </div>
      <div style={{ marginTop: 18 }}>
        <button type="button" className="result-link" onClick={onRetry}>
          <RetryIcon />
          RETRY
        </button>
      </div>
    </Card>
  );
}

/* ---- inline icons ----------------------------------------------------- */

function InfoIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="8" x2="12" y2="13" />
      <circle cx="12" cy="16.5" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

function RetryIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <polyline points="3 4 3 8 7 8" />
    </svg>
  );
}
