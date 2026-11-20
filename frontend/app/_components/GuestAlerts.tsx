"use client";

/**
 * "Email me when a table opens" for guests: no account, just an address they
 * confirm by link (roadmap 3b). Signed-in visitors don't see it; their alerts
 * go to the account's address. Hidden too when the server can't store an
 * address (no PostgreSQL), so it never offers something that won't work.
 */

import { useEffect, useId, useState } from "react";
import { getAlertEmail, removeAlertEmail, requestAlertEmail } from "@/lib/api";
import { useOptionalAuth } from "./AuthProvider";

type State =
  | { phase: "loading" }
  | { phase: "hidden" }
  | { phase: "ask"; error?: string; busy?: boolean }
  | { phase: "sent"; email: string }
  | { phase: "on"; email: string; busy?: boolean; error?: string };

export function GuestAlerts() {
  const auth = useOptionalAuth();
  const signedIn = auth?.user != null;
  const id = useId();
  const [state, setState] = useState<State>({ phase: "loading" });
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (signedIn) return;
    let live = true;
    void getAlertEmail().then((result) => {
      if (!live) return;
      if (!result?.ok) {
        setState({ phase: "hidden" });
      } else if (result.data.email) {
        setState({ phase: "on", email: result.data.email });
      } else {
        setState({ phase: "ask" });
      }
    });
    return () => {
      live = false;
    };
  }, [signedIn]);

  if (signedIn || auth?.loading) return null;
  if (state.phase === "loading" || state.phase === "hidden") return null;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (address === "") return;
    setState({ phase: "ask", busy: true });
    const result = await requestAlertEmail(address);
    setState(
      result.ok ? { phase: "sent", email: address } : { phase: "ask", error: result.message },
    );
  }

  async function onStop() {
    if (state.phase !== "on") return;
    setState({ ...state, busy: true, error: undefined });
    const result = await removeAlertEmail();
    if (result.ok) {
      setEmail("");
      setState({ phase: "ask" });
    } else {
      setState({ ...state, busy: false, error: result.message });
    }
  }

  if (state.phase === "on") {
    return (
      <div className="guest-alerts" role="status">
        <span className="guest-alerts-text">
          Alerts go to <strong>{state.email}</strong>.
        </span>
        <button
          type="button"
          className="result-link"
          disabled={state.busy}
          onClick={() => void onStop()}
        >
          {state.busy ? "STOPPING…" : "STOP EMAILS"}
        </button>
        {state.error && (
          <div className="form-error" role="alert">
            {state.error}
          </div>
        )}
      </div>
    );
  }

  if (state.phase === "sent") {
    return (
      <div className="guest-alerts" role="status">
        <span className="guest-alerts-text">
          Check your inbox: we sent a link to <strong>{state.email}</strong>. Open
          it to start alerts. It expires in 24 hours.
        </span>
      </div>
    );
  }

  return (
    <form className="guest-alerts" onSubmit={(e) => void onSubmit(e)}>
      <label className="form-label" htmlFor={`${id}-email`}>
        EMAIL ME WHEN A TABLE OPENS
      </label>
      <div className="guest-alerts-row">
        <input
          id={`${id}-email`}
          className="form-input"
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          disabled={state.busy}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button
          type="submit"
          className="result-link"
          disabled={state.busy || email.trim() === ""}
        >
          {state.busy ? "SENDING…" : "SEND LINK ▸"}
        </button>
      </div>
      <span className="guest-alerts-note">
        No account needed. We email you a link to confirm, then only alerts for
        this browser&rsquo;s watches.
      </span>
      {state.error && (
        <div className="form-error" role="alert">
          {state.error}
        </div>
      )}
    </form>
  );
}
