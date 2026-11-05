"use client";

/**
 * The interactive half of the watch dashboard. It is the only component here
 * that holds state or calls the API: it loads the calling client's own watches
 * on mount (Requirement 4.1), cancels one in place without a reload
 * (Requirement 4.5), and keeps the "next check" countdowns honest with a slow
 * tick. Everything it shows goes through the pure `WatchCard`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
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
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const mounted = useRef(false);
  const requestVersion = useRef(0);
  const inFlight = useRef<number | null>(null);
  const mutations = useRef(0);

  const load = useCallback(async (initial = false) => {
    if (mutations.current > 0 || (!initial && inFlight.current !== null)) return;
    const version = ++requestVersion.current;
    inFlight.current = version;
    if (initial) setState({ phase: "loading" });
    const result = await listMyWatches();
    if (inFlight.current === version) inFlight.current = null;
    if (!mounted.current || version !== requestVersion.current) return;
    if (result.ok) {
      setState({ phase: "ready", watches: mostRecentFirst(result.data) });
      setRefreshError(null);
    } else if (initial) {
      setState({ phase: "error", message: result.message });
    } else {
      setRefreshError(result.message);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load(true);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    const id = setInterval(() => {
      setNow(Date.now());
      refreshVisible();
    }, 30_000);
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      mounted.current = false;
      requestVersion.current++;
      inFlight.current = null;
      clearInterval(id);
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [load]);

  async function onCancel(watchId: string) {
    mutations.current++;
    requestVersion.current++; // An earlier refresh cannot undo cancellation.
    inFlight.current = null;
    setCancelError(null);
    setCancelling((prev) => new Set(prev).add(watchId));

    const result = await cancelWatch(watchId);
    mutations.current--;
    if (!mounted.current) return;

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

  const refreshBanner = refreshError && (
    <div className="dash-banner" role="alert">
      Refresh failed: {refreshError} Retrying automatically.
    </div>
  );

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
          <button type="button" className="result-link" onClick={() => void load(true)}>
            RETRY
          </button>
        </div>
      </div>
    );
  }

  if (state.watches.length === 0) {
    return (
      <>
      {refreshBanner}
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
      </>
    );
  }

  return (
    <>
      {refreshBanner}
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
