"use client";

/**
 * The sign-in / sign-up form (Requirement 5.1).
 *
 * One form, two modes — the fields are identical, so a toggle beats a second
 * component. Failures render the backend's plain-language `detail` and nothing
 * else: no stack trace, no raw JSON, and never the password (Requirement 5.5).
 */

import { useState } from "react";
import { useAuth } from "./AuthProvider";

type Mode = "login" | "signup";

const COPY: Record<Mode, { action: string; busy: string; alt: string; altCta: string }> = {
  login: {
    action: "SIGN IN ▸",
    busy: "SIGNING IN…",
    alt: "No account yet?",
    altCta: "Create one",
  },
  signup: {
    action: "CREATE ACCOUNT ▸",
    busy: "CREATING…",
    alt: "Already have an account?",
    altCta: "Sign in",
  },
};

export function AuthForm() {
  const { user, login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const copy = COPY[mode];
  const ready = email.trim().length > 0 && password.length > 0;

  if (user !== null) {
    return (
      <div className="auth-panel" role="status">
        <div className="kicker" data-tone="green">
          SIGNED IN
        </div>
        <div className="result-title sm" style={{ marginTop: 8 }}>
          {user.email}
        </div>
        <p className="auth-note">
          Your watches now follow this account on any device you sign in from.
        </p>
        <a className="empty-cta" href="/watches">
          SEE YOUR WATCHES ▸
        </a>
      </div>
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;

    setBusy(true);
    setError(null);
    const result = await (mode === "login" ? login : signup)(email, password);
    setBusy(false);

    if (result.ok) {
      // A full load, matching how the rest of the app navigates, so the
      // dashboard mounts fresh against the new identity (Requirement 5.4).
      window.location.assign("/watches");
      return;
    }
    setError(result.message);
    setPassword("");
  }

  function switchMode() {
    setMode((m) => (m === "login" ? "signup" : "login"));
    setError(null);
  }

  return (
    <div className="auth-panel">
      <form className="auth-form" onSubmit={onSubmit} data-busy={busy}>
        <label className="auth-label" htmlFor="email">
          EMAIL
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          className="auth-input"
          value={email}
          disabled={busy}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label className="auth-label" htmlFor="password">
          PASSWORD
        </label>
        <input
          id="password"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          className="auth-input"
          value={password}
          disabled={busy}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}

        <button type="submit" className="acquire" disabled={busy || !ready}>
          {busy ? copy.busy : copy.action}
        </button>
      </form>

      <p className="auth-alt">
        {copy.alt}{" "}
        <button type="button" className="result-link" onClick={switchMode}>
          {copy.altCta}
        </button>
      </p>

      <p className="auth-note">
        Signing in claims the watches you opened on this device, so they stay
        yours on any other.
      </p>
    </div>
  );
}
