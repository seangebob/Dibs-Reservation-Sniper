"use client";

/**
 * Where an account confirmation email lands: use the link's code once, then
 * say the address is confirmed. The code is removed from the address bar so
 * it doesn't sit in the browser's history.
 */

import { useEffect, useRef, useState } from "react";
import { confirmEmail } from "@/lib/api";

type State =
  | { phase: "checking" }
  | { phase: "done"; email: string }
  | { phase: "failed"; message: string };

export function ConfirmEmail() {
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
    void confirmEmail(token).then((result) => {
      setState(
        result.ok
          ? { phase: "done", email: result.data.email }
          : { phase: "failed", message: result.message },
      );
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
        <p className="guest-alerts-text" style={{ marginTop: 10 }}>
          Signed in? Your account page can send a new link.
        </p>
        <div style={{ marginTop: 16 }}>
          <a className="result-link" href="/account">
            GO TO YOUR ACCOUNT ▸
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
        {state.email} will get your alerts.
      </div>
      <div style={{ marginTop: 16 }}>
        <a className="result-link" href="/watches">
          GO TO YOUR WATCHES ▸
        </a>
      </div>
    </div>
  );
}
