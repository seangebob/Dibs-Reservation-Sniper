"use client";

/**
 * The interactive half of the watch dashboard. It is the only component here
 * that holds state or calls the API: it loads the calling client's own watches
 * on mount (Requirement 4.1), cancels one in place without a reload
 * (Requirement 4.5), and keeps the "next check" countdowns honest with a slow
 * tick. Everything it shows goes through the pure `WatchCard`.
 */

import { useCallback, useEffect, useState } from "react";
import { cancelWatch, listMyWatches } from "@/lib/api";
import type { Watch } from "@/types/api";
import { WatchCard } from "./WatchCard";

type DashState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; watches: Watch[] };

/** Most recent first (Requirement 4.1), by when the watch was opened. */
function mostRecentFirst(watches: Watch[]): Watch[] {
  return [...watches].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  );
}

export function WatchDashboard() {
  const [state, setState] = useState<DashState>({ phase: "loading" });
  const [cancelling, setCancelling] = useState<ReadonlySet<string>>(new Set());
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    setState({ phase: "loading" });
    setCancelError(null);
    void listMyWatches().then((result) => {
      setState(
        result.ok
          ? { phase: "ready", watches: mostRecentFirst(result.data) }
          : { phase: "error", message: result.message },
      );
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Refresh the injected clock so open "next check in …" countdowns don't go
  // stale while the page is left open. 30s is well under the poll cadence.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  async function onCancel(watchId: string) {
    setCancelError(null);
    setCancelling((prev) => new Set(prev).add(watchId));

    const result = await cancelWatch(watchId);

    setCancelling((prev) => {
      const next = new Set(prev);
      next.delete(watchId);
      return next;
    });

    if (result.ok) {
      // Replace the watch with its resolved (CANCELLED) state in place — no
      // refetch, so the card updates and its cancel action falls away.
      const updated = result.data;
      setState((s) =>
        s.phase === "ready"
          ? {
              ...s,
              watches: s.watches.map((w) =>
                w.watch_id === watchId ? updated : w,
              ),
            }
          : s,
      );
    } else {
      setCancelError(result.message);
    }
  }

  if (state.phase === "loading") {
    return (
      <div className="dash-status" role="status" aria-live="polite">
        <span className="telemetry">
          <span className="scanline" aria-hidden="true" />
          SWEEPING FOR WATCHES…
        </span>
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="dash-status" role="alert">
        <div className="kicker" data-tone="red">
          SIGNAL LOST
        </div>
        <div className="result-title sm" style={{ marginTop: 8 }}>
          {state.message}
        </div>
        <div style={{ marginTop: 16 }}>
          <button type="button" className="result-link" onClick={load}>
            RETRY
          </button>
        </div>
      </div>
    );
  }

  if (state.watches.length === 0) {
    return (
      <div className="empty">
        <div className="empty-title">No watches on the board.</div>
        <p className="empty-text">
          Nothing in the crosshairs yet. Describe a reservation and Dibs will
          keep it in the scope until a table opens.
        </p>
        <a className="empty-cta" href="/">
          OPEN A WATCH ▸
        </a>
      </div>
    );
  }

  return (
    <>
      {cancelError && (
        <div className="dash-banner" role="alert">
          {cancelError}
        </div>
      )}
      <div className="watch-list">
        {state.watches.map((watch) => (
          <WatchCard
            key={watch.watch_id}
            watch={watch}
            now={now}
            cancelling={cancelling.has(watch.watch_id)}
            onCancel={onCancel}
          />
        ))}
      </div>
    </>
  );
}
