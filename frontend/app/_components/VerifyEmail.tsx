"use client";

/**
 * The page a confirmation email links to: use the link's token once, then say
 * where alerts will go. The token is read from the address bar and removed
 * from it, so it doesn't sit in the browser's history.
 */

import { useEffect, useRef, useState } from "react";
import { verifyAlertEmail } from "@/lib/api";

type State =
  | { phase: "checking" }
  | { phase: "done"; email: string }
  | { phase: "failed"; message: string };

export function VerifyEmail() {
  const [state, setState] = useState<State>({ phase: "checking" });
  // React's development double-run of effects must not spend the link twice.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const token = new URLSearchParams(window.location.search).get("token")?.trim();
    window.history.replaceState(null, "", window.location.pathname);
    if (!token) {
      setState({
        phase: "failed",
        message: "This link is missing its code. Open the link from the email again.",
      });
      return;
    }
    void verifyAlertEmail(token).then((result) => {
      if (result.ok && result.data.email) {
        setState({ phase: "done", email: result.data.email });
      } else {
        setState({
          phase: "failed",
          message: result.ok ? "Something went wrong. Ask for a new link." : result.message,
        });
      }
    });
  }, []);

  if (state.phase === "checking") {
    return (
      <div className="dash-status" role="status" aria-live="polite">
        <span className="telemetry">
          <span className="scanline" aria-hidden="true" />
          CONFIRMING…
        </span>
      </div>
    );
  }

  if (state.phase === "failed") {
    return (
      <div className="dash-status" role="alert">
        <div className="kicker" data-tone="red">
          LINK DIDN&rsquo;T WORK
        </div>
        <div className="result-title sm" style={{ marginTop: 8 }}>
          {state.message}
        </div>
        <div style={{ marginTop: 16 }}>
          <a className="result-link" href="/watches">
            GO TO YOUR WATCHES ▸
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="dash-status" role="status">
      <div className="kicker" data-tone="green">
        EMAIL CONFIRMED
      </div>
      <div className="result-title sm" style={{ marginTop: 8 }}>
        Alerts will go to {state.email}.
      </div>
      <p className="guest-alerts-text" style={{ marginTop: 10 }}>
        Dibs emails you when a table opens for a watch made in the browser where
        you asked for this link.
      </p>
      <div style={{ marginTop: 16 }}>
        <a className="result-link" href="/watches">
          GO TO YOUR WATCHES ▸
        </a>
      </div>
    </div>
  );
}
